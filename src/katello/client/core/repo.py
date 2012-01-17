#
# Katello Repos actions
# Copyright (c) 2010 Red Hat, Inc.
#
# This software is licensed to you under the GNU General Public License,
# version 2 (GPLv2). There is NO WARRANTY for this software, express or
# implied, including the implied warranties of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. You should have received a copy of GPLv2
# along with this software; if not, see
# http://www.gnu.org/licenses/old-licenses/gpl-2.0.txt.
#
# Red Hat trademarks are not licensed under GPLv2. No permission is
# granted to use or replicate Red Hat trademarks that are incorporated
# in this software or its documentation.
#

import os
import urlparse
from gettext import gettext as _

from katello.client import constants
from katello.client.core.utils import format_date
from katello.client.api.repo import RepoAPI
from katello.client.config import Config
from katello.client.core.base import Action, Command
from katello.client.api.utils import get_environment, get_product, get_repo
from katello.client.core.utils import system_exit, run_async_task_with_status, run_spinner_in_bg, wait_for_async_task, AsyncTask, format_progress_errors, format_task_errors
from katello.client.core.utils import ProgressBar

try:
    import json
except ImportError:
    import simplejson as json

Config()

SYNC_STATES = { 'waiting':     _("Waiting"),
                'running':     _("Running"),
                'error':       _("Error"),
                'finished':    _("Finished"),
                'cancelled':   _("Cancelled"),
                'canceled':    _("Canceled"),
                'timed_out':   _("Timed out"),
                'not_synced':  _("Not synced") }


def format_sync_time(sync_time):
    if sync_time is None:
        return 'never'
    else:
        return str(format_date(sync_time[0:19], '%Y-%m-%dT%H:%M:%S'))
        #'2011-07-11T15:03:52+02:00

def format_sync_state(state):
    return SYNC_STATES[state]

# base action ----------------------------------------------------------------

class RepoAction(Action):

    def __init__(self):
        super(RepoAction, self).__init__()
        self.api = RepoAPI()

    def get_repo(self, includeDisabled=False):
        repoId   = self.get_option('id')
        repoName = self.get_option('name')
        orgName  = self.get_option('org')
        prodName = self.get_option('product')
        envName  = self.get_option('env')

        if repoId:
            repo = self.api.repo(repoId)
        else:
            repo = get_repo(orgName, prodName, repoName, envName, includeDisabled)

        return repo

class SingleRepoAction(RepoAction):

    select_by_env = False

    def setup_parser(self):
        self.set_repo_select_options(self.select_by_env)

    def check_options(self):
        self.check_repo_select_options()

    def set_repo_select_options(self, select_by_env=True):
        self.parser.add_option('--id', dest='id', help=_("repository id"))
        self.parser.add_option('--name', dest='name', help=_("repository name"))
        self.parser.add_option('--org', dest='org', help=_("organization name eg: foo.example.com"))
        self.parser.add_option('--product', dest='product', help=_("product name eg: fedora-14"))
        if select_by_env:
            self.parser.add_option('--environment', dest='env', help=_("environment name eg: production (default: Locker)"))

    def check_repo_select_options(self):
        if not self.has_option('id'):
            self.require_option('name')
            self.require_option('org')
            self.require_option('product')




# actions --------------------------------------------------------------------


class Create(RepoAction):

    description = _('create a repository at a specified URL')

    def setup_parser(self):
        self.parser.add_option('--org', dest='org',
                               help=_("organization name eg: foo.example.com (required)"))
        self.parser.add_option('--name', dest='name',
                               help=_("repository name to assign (required)"))
        self.parser.add_option("--url", dest="url",
                               help=_("url path to the repository (required)"))
        self.parser.add_option('--product', dest='prod',
                               help=_("product name (required)"))
        self.parser.add_option('--gpgkey', dest='gpgkey',
                               help=_("GPG key to be assigned to the repository; by default, the product's GPG key will be used."))
        self.parser.add_option('--nogpgkey', action='store_true',
                               help=_("Don't assign a GPG key to the repository."))

    def check_options(self):
        self.require_option('org')
        self.require_option('name')
        self.require_option('url')
        self.require_option('prod')

    def run(self):
        name     = self.get_option('name')
        url      = self.get_option('url')
        prodName = self.get_option('prod')
        orgName  = self.get_option('org')
        gpgkey   = self.get_option('gpgkey')
        nogpgkey   = self.get_option('nogpgkey')


        prod = get_product(orgName, prodName)
        if prod != None:
            repo = self.api.create(prod["id"], name, url, gpgkey, nogpgkey)
            print _("Successfully created repository [ %s ]") % name
        else:
            print _("No product [ %s ] found") % prodName
            return os.EX_DATAERR

        return os.EX_OK

class Discovery(RepoAction):

    description = _('discovery repositories contained within a URL')

    def setup_parser(self):
        self.parser.add_option('--org', dest='org',
                               help=_("organization name eg: foo.example.com (required)"))
        self.parser.add_option('--name', dest='name',
                               help=_("repository name prefix to add to all the discovered repositories (required)"))
        self.parser.add_option("--url", dest="url",
                               help=_("root url to perform discovery of repositories eg: http://porkchop.devel.redhat.com/ (required)"))
        self.parser.add_option("--assumeyes", action="store_true", dest="assumeyes",
                               help=_("assume yes; automatically create candidate repositories for discovered urls (optional)"))
        self.parser.add_option('--product', dest='prod',
                               help=_("product name (required)"))

    def check_options(self):
        self.require_option('org')
        self.require_option('name')
        self.require_option('url')
        self.require_option('prod')

    def run(self):
        name     = self.get_option('name')
        url      = self.get_option('url')
        assumeyes = self.get_option('assumeyes')
        prodName = self.get_option('prod')
        orgName  = self.get_option('org')

        repourls = self.discover_repositories(orgName, url)
        self.printer.setHeader(_("Repository Urls discovered @ [%s]" % url))
        selectedurls = self.select_repositories(repourls, assumeyes)

        prod = get_product(orgName, prodName)
        if prod != None:
            self.create_repositories(prod["id"], name, selectedurls)

        return os.EX_OK

    def discover_repositories(self, org_name, url):
        print(_("Discovering repository urls, this could take some time..."))
        try:
            task = self.api.repo_discovery(org_name, url, 'yum')
        except Exception,e:
            system_exit(os.EX_DATAERR, _("Error: %s" % e))

        discoveryResult = run_spinner_in_bg(wait_for_async_task, [task])
        repourls = discoveryResult[0]['result'] or []

        if not len(repourls):
            system_exit(os.EX_OK, "No repositories discovered @ url location [%s]" % url)

        return repourls


    def select_repositories(self, repourls, assumeyes, raw_input = raw_input):
        selection = Selection()
        if not assumeyes:
            proceed = ''
            num_selects = [str(i+1) for i in range(len(repourls))]
            select_range_str = constants.SELECTION_QUERY % len(repourls)
            while proceed.strip().lower() not in  ['q', 'y']:
                if not proceed.strip().lower() == 'h':
                    self.__print_urls(repourls, selection)
                proceed = raw_input(_("\nSelect urls for which candidate repos should be created; use `y` to confirm (h for help):"))
                select_val = proceed.strip().lower()
                if select_val == 'h':
                    print select_range_str
                elif select_val == 'a':
                    selection.add_selection(repourls)
                elif select_val in num_selects:
                    selection.add_selection([repourls[int(proceed.strip().lower())-1]])
                elif select_val == 'q':
                    selection = Selection()
                    system_exit(os.EX_OK, _("Operation aborted upon user request."))
                elif set(select_val.split(":")).issubset(num_selects):
                    lower, upper = tuple(select_val.split(":"))
                    selection.add_selection(repourls[int(lower)-1:int(upper)])
                elif select_val == 'c':
                    selection = Selection()
                elif select_val == 'y':
                    if not len(selection):
                        proceed = ''
                        continue
                    else:
                        break
                else:
                    continue
        else:
            #select all
            selection.add_selection(repourls)
            self.__print_urls(repourls, selection)

        return selection

    def create_repositories(self, productid, name, selectedurls):
        for repourl in selectedurls:
            parsedUrl = urlparse.urlparse(repourl)
            repoName = self.repository_name(name, parsedUrl.path) # pylint: disable=E1101
            repo = self.api.create(productid, repoName, repourl, None, None)

            print _("Successfully created repository [ %s ]") % repoName

    def repository_name(self, name, parsedUrlPath):
        return "%s%s" % (name, parsedUrlPath.replace("/", "_"))

    def __print_urls(self, repourls, selectedurls):
        for index, url in enumerate(repourls):
            if url in selectedurls:
                print "(+)  [%s] %-5s" % (index+1, url)
            else:
                print "(-)  [%s] %-5s" % (index+1, url)


class Selection(list):
    def add_selection(self, urls):
        for url in urls:
            if url not in self:
                self.append(url)


class Status(SingleRepoAction):

    description = _('status information about a repository')
    select_by_env = True

    def run(self):
        repo = self.get_repo()
        if repo == None:
            return os.EX_DATAERR

        task = AsyncTask(self.api.last_sync_status(repo['id']))

        repo['last_sync'] = format_sync_time(repo['last_sync'])
        repo['sync_state'] = format_sync_state(repo['sync_state'])
        if task.is_running():
            pkgsTotal = task.total_count()
            pkgsLeft = task.items_left()
            repo['progress'] = ("%d%% done (%d of %d packages downloaded)" % (task.get_progress()*100, pkgsTotal-pkgsLeft, pkgsTotal))

        errors = task.progress_errors()
        if len(errors) > 0:
            repo['last_errors'] = format_progress_errors(errors)

        self.printer.addColumn('package_count')
        self.printer.addColumn('last_sync')
        self.printer.addColumn('sync_state')
        self.printer.addColumn('progress', show_in_grep=False)
        self.printer.addColumn('last_errors', multiline=True, show_in_grep=False)

        self.printer.setHeader(_("Repository Status"))
        self.printer.printItem(repo)
        return os.EX_OK


class Info(SingleRepoAction):

    description = _('information about a repository')
    select_by_env = True

    def run(self):
        repo = self.get_repo(True)
        if repo == None:
            return os.EX_DATAERR

        repo['url'] = repo['source']['url']
        repo['last_sync'] = format_sync_time(repo['last_sync'])
        repo['sync_state'] = format_sync_state(repo['sync_state'])

        self.printer.addColumn('id')
        self.printer.addColumn('name')
        self.printer.addColumn('package_count')
        self.printer.addColumn('arch', show_in_grep=False)
        self.printer.addColumn('url', show_in_grep=False)
        self.printer.addColumn('last_sync', show_in_grep=False)
        self.printer.addColumn('sync_state', name=_("Progress"), show_in_grep=False)
        self.printer.addColumn('gpg_key_name', name=_("GPG key"), show_in_grep=False)

        self.printer.setHeader(_("Information About Repo %s") % repo['id'])

        self.printer.printItem(repo)
        return os.EX_OK

class Update(SingleRepoAction):

    description = _('updates repository attributes')
    select_by_env = True

    def setup_parser(self):
        super(Update, self).setup_parser()
        self.parser.add_option('--gpgkey', dest='gpgkey',
                               help=_("GPG key to be assigned to the repository; by default, the product's GPG key will be used."))
        self.parser.add_option('--nogpgkey', action='store_true',
                               help=_("Don't assign a GPG key to the repository."))

    def run(self):
        repo = self.get_repo(True)
        gpgkey   = self.get_option('gpgkey')
        nogpgkey   = self.get_option('nogpgkey')
        if repo == None:
            return os.EX_DATAERR

        self.api.update(repo['id'], gpgkey, nogpgkey)
        print _("Successfully updated repository [ %s ]") % repo['name']
        return os.EX_OK


class Sync(SingleRepoAction):

    description = _('synchronize a repository')
    select_by_env = False

    def run(self):
        repo = self.get_repo()
        if repo == None:
            return os.EX_DATAERR

        task = AsyncTask(self.api.sync(repo['id']))
        run_async_task_with_status(task, ProgressBar())

        if task.succeeded():
            print _("Repo [ %s ] synced" % repo['name'])
            return os.EX_OK
        elif task.cancelled():
            print _("Repo [ %s ] synchronization cancelled" % repo['name'])
            return os.EX_OK
        else:
            print _("Repo [ %s ] failed to sync: %s" % (repo['name'], format_task_errors(task.errors())) )
            return os.EX_DATAERR


class CancelSync(SingleRepoAction):

    description = _('cancel currently running synchronization of a repository')
    select_by_env = False

    def run(self):
        repo = self.get_repo()
        if repo == None:
            return os.EX_DATAERR

        msg = self.api.cancel_sync(repo['id'])
        print msg
        return os.EX_OK

class Enable(SingleRepoAction):

    @property
    def description(self):
        if self._enable:
            return _('enable a repository')
        else:
            return _('disable a repository')

    select_by_env = False

    def __init__(self, enable = True):
        self._enable = enable
        super(Enable, self).__init__()

    def run(self):
        repo = self.get_repo(True)
        if repo == None:
            return os.EX_DATAERR

        msg = self.api.enable(repo["id"], self._enable)
        print msg

        return os.EX_OK


class List(RepoAction):

    description = _('list repos within an organization')

    def setup_parser(self):
        self.parser.add_option('--org', dest='org',
            help=_("organization name eg: ACME_Corporation (required)"))
        self.parser.add_option('--environment', dest='env',
            help=_("environment name eg: production (default: locker)"))
        self.parser.add_option('--product', dest='product',
            help=_("product name eg: fedora-14"))
        self.parser.add_option('--include_disabled', action="store_true", dest='disabled',
            help=_("list also disabled repositories"))

    def check_options(self):
        self.require_option('org')

    def run(self):
        orgName = self.get_option('org')
        envName = self.get_option('env')
        prodName = self.get_option('product')
        listDisabled = self.has_option('disabled')

        self.printer.addColumn('id')
        self.printer.addColumn('name')
        self.printer.addColumn('package_count')

        if prodName and envName:
            env  = get_environment(orgName, envName)
            prod = get_product(orgName, prodName)
            if env != None and prod != None:
                self.printer.setHeader(_("Repo List For Org %s Environment %s Product %s") % (orgName, env["name"], prodName))
                repos = self.api.repos_by_env_product(env["id"], prod["id"], None, listDisabled)
                self.printer.printItems(repos)
        elif prodName:
            prod = get_product(orgName, prodName)
            if prod != None:
                self.printer.setHeader(_("Repo List for Product %s in Org %s ") % (prodName, orgName))
                repos = self.api.repos_by_product(prod["id"], listDisabled)
                self.printer.printItems(repos)
            else:
                return os.EX_DATAERR
        else:
            env  = get_environment(orgName, envName)
            if env != None:
                self.printer.setHeader(_("Repo List For Org %s Environment %s") % (orgName, env["name"]))
                repos = self.api.repos_by_org_env(orgName,  env["id"], listDisabled)
                self.printer.printItems(repos)

        return os.EX_OK


class Delete(SingleRepoAction):

    description = _('delete a repository')
    select_by_env = True

    def run(self):
        repo = self.get_repo()
        if repo == None:
            return os.EX_DATAERR

        msg = self.api.delete(repo["id"])
        print msg
        return os.EX_OK


# command --------------------------------------------------------------------

class Repo(Command):

    description = _('repo specific actions in the katello server')
