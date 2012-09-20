#!/usr/bin/python2.7
#
# Pikpoint - OmniFocus to AgileZen (GTD to Personal Kanban) synchronizer
# Copyright (C) 2012  Romain Lenglet
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.


import argparse
import datetime
import logging
import os

import appscript

import agilezen
import omnifocus


AGILEZEN_API_BASE_URL = 'https://agilezen.com/api/v1/'

DUE_DATE_FORMAT = '%a %b %d %I:%M%p %Y'
DUE_SOON_DAYS = 3

LOG = logging.getLogger('omnifocus2agilezen')


class OmniFocusToAgileZenSync(object):
    """A synchronizer between OmniFocus and AgileZen.
    """

    def __init__(self, omnifocus_dao, agilezen_dao,
                 due_soon_days=DUE_SOON_DAYS):
        """Initialize this synchronizer with the OF and AZ DAOs.

        Args:
            omnifocus_dao: The OmniFocusDataAccess object to use to
                access the OmniFocus database.
            agilezen_dao: The AgileZenDataAccess object to use to
                access the AgileZen database.
            due_soon_days: The number of days in the future that is
                the limit deadline for due dates to become "due soon".
                Defaults to 3.
        """
        self.of_dao = omnifocus_dao
        self.az_dao = agilezen_dao
        self.due_soon_delta = datetime.timedelta(days=3)

    @classmethod
    def _get_az_story_details(cls, details, of_id):
        """Gets an AgileZen story's details from free text details and an ID.

        Args:
            details: The free text details to store in the story's details.
            of_id: An OmniFocus ID associated with the story, and to
                be stored in the story's details.

        Returns:
            An AgileZen story's details containing free text details
            and an associated OmniFocus ID.
        """
        return '%s\n[id](%s)' % (details, of_id)

    def _get_az_story_text_for_project(self, of_project):
        """Gets an AgileZen story's text from an OmniFocus project.

        Args:
            of_project: The OmniFocus project to get information from.

        Returns:
            An AgileZen story's text containing information about the
            OmniFocus project.
        """
        elements = ['**%s**' % (of_project.name.strip(' '),)]
        full_folder_name = of_project.full_folder_name.strip(' ')
        if full_folder_name:
            elements.append(full_folder_name)
        due_date = of_project.due_date
        if due_date:
            due_date_txt = 'Due ' + due_date.strftime(DUE_DATE_FORMAT)
            # Make it bold if the deadline is soon (in 3 days or less).
            if due_date < (datetime.datetime.now() + self.due_soon_delta):
                due_date_txt = '**%s**' % (due_date_txt,)
            elements.append(due_date_txt)
        return '\n'.join(elements)

    @classmethod
    def _get_az_story_details_for_project(cls, of_project):
        """Gets an AgileZen story's details from an OmniFocus project.

        Args:
            of_project: The OmniFocus project to get details from.

        Returns:
            An AgileZen story's details containing details about the
            OmniFocus project, and the project's ID.
        """
        return cls._get_az_story_details(of_project.note,
                                         of_project.id)

    @classmethod
    def _get_az_tags_for_project(cls, of_project):
        """Gets the AgileZen tags matching the contexts of an OmniFocus project.

        Args:
            of_project: The OmniFocus project to get tags from.

        Returns:
            The set of AgileZen tags corresponding the contexts of the
            actions of the OmniFocus project.
        """
        tag_names = set()
        for task in of_project.root_task.tasks:
            all_full_context_names = task.all_full_context_names
            if all_full_context_names:
                tag_names.update([n.strip(' ').lower()
                                  for n in all_full_context_names])
        return set([agilezen.Tag(None, tag_name) for tag_name in tag_names])

    @classmethod
    def _get_omnifocus_id(cls, az_story):
        """Gets the OmniFocus ID stored in an AgileZen story's details.

        Args:
            story: The AgileZen story to get an OmniFocus ID from.

        Returns:
            The OmniFocus ID as a string, or None if not found.
        """
        details = az_story.details
        if not details:
            return None
        last_line = details[details.rfind('\n')+1:]
        if not last_line.startswith('[id](') or not last_line.endswith(')'):
            return None
        return last_line[5:-1]

    @staticmethod
    def _get_az_story_phase_for_project(of_project, az_phases,
                                        current_az_phase):
        """Gets the phase of an AgileZen story for an OmniFocus project.

        Args:
            of_project: The OmniFocus project to get tasks from.
            az_phases: The ProjectPhases object containing the key
                phases of the AgileZen project.
            current_az_phase: The current phase of the AgileZen story.

        Returns:
            The new phase of the AgileZen story for the given
            OmniFocus project.
        """
        if of_project.status == appscript.k.on_hold:
            if current_az_phase.id == az_phases.ready.id:
                return current_az_phase
            else:
                return az_phases.backlog
        elif of_project.completed:
            if current_az_phase.id in (az_phases.done.id, az_phases.archive.id):
                # Already completed too.
                return current_az_phase
            else:
                return az_phases.done
        else:  # Active, not completed.
            if current_az_phase.id in (az_phases.backlog.id,
                                       az_phases.ready.id):
                return az_phases.first_in_progress
            else:
                return current_az_phase

    @staticmethod
    def _get_az_tasks_for_project(of_project):
        """Gets a list of AgileZen tasks from an OmniFocus project's tasks.

        Tasks are de-duplicated: only the first task in an OmniFocus
        project is kept, the subsequenct tasks are ignored.

        Args:
            of_project: The OmniFocus project to get tasks from.

        Returns:
            The list of Task objects corresponding to the OmniFocus
            project's non-completed tasks, each with a unique text.
        """
        # Task text is plain text, not Markdown, so we can't hide any
        # OmniFocus task ID in there.  We rely on text comparisons to
        # synchronize tasks.  So first remove any duplicate tasks
        # texts.
        task_names_dups = [(task.name, task.completed)
                           for task in of_project.root_task.tasks]
        task_names_set = set()
        tasks = []
        for task_name, task_completed in task_names_dups:
            if task_name not in task_names_set:
                task_names_set.add(task_name)
                tasks.append(agilezen.Task(None, task_name, None, None, None,
                                           task_completed))
        return tasks

    def sync_projects(self, of_project_selector, of_color_picker,
                      az_project_id, owner_username=None):
        """Synchronizes OmniFocus projects as AgileZen stories.

        Every OmniFocus project corresponds to one story in an
        AgileZen project.

        Args:
            of_project_selector: A callable taking an OmniFocus
                project object, and returns True or False whether the
                project must be synchronized or not.
            of_color_picker: A callable taking an OmniFocus project
                object, and returns the color of the corresponding
                story card, as a string.  The returned color must be
                in the agilezen.COLORS list.
            az_project_id: The ID of the AgileZen project to contain
                the stories.
            owner_username: The username of the owner to assign to all
                AgileZen stories.  Defaults to None, i.e. no owner.
        """
        owner = None
        if owner_username:
            owner = agilezen.User(None, None, None, owner_username)

        az_project = None
        try:
            az_project = self.az_dao.get_project(az_project_id)
        except Exception:
            LOG.error('project ID %i not found', az_project_id)
            raise ValueError('project ID %i not found' % (az_project_id,))

        az_phases = agilezen.ProjectPhases.parse_phases(
            list(self.az_dao.iter_project_phases(az_project.id)))

        of_projects_dict = self.of_dao.get_projects(of_project_selector)
        az_stories = list(self.az_dao.iter_project_stories(
                az_project.id, with_details=True, with_tags=True,
                with_tasks=True))

        # Delete stories that have no OmniFocus project ID.
        for az_story in az_stories:
            if self._get_omnifocus_id(az_story) is None:
                LOG.debug('deleting AgileZen story %s "%s"',
                          az_story.id, az_story.text)
                self.az_dao.delete_project_story(az_project.id, az_story.id)

        # TODO: Check for duplicates, i.e. multiple stories with the
        # same OmniFocus ID.

        az_stories_dict = dict([(self._get_omnifocus_id(story), story)
                                for story in az_stories])
        if None in az_stories_dict:
            del az_stories_dict[None]  # Already deleted above.

        of_project_ids = set(of_projects_dict.iterkeys())
        az_of_project_ids = set(az_stories_dict.iterkeys())

        # Collect the current and final sets of tags, to delete unused tags.
        all_used_tags = set()

        # Add new AZ stories for new OF projects.
        for of_project_id in of_project_ids - az_of_project_ids:
            _, of_project = of_projects_dict[of_project_id]
            az_story = agilezen.Story(
                None,
                self._get_az_story_text_for_project(of_project),
                self._get_az_story_details_for_project(of_project),
                None,
                None,
                of_color_picker(of_project),
                self._get_az_story_phase_for_project(of_project, az_phases,
                                                     az_phases.backlog),
                None,
                owner,
                self._get_az_tags_for_project(of_project),
                self._get_az_tasks_for_project(of_project))
            all_used_tags.update(az_story.tags)
            LOG.debug('creating AgileZen story "%s"', az_story.text)
            self.az_dao.create_project_story(az_project.id, az_story)

        # TODO: Copy the project's "estimated_minutes" into the
        # story's size.

        for of_project_id in az_of_project_ids:
            az_story = az_stories_dict[of_project_id]
            of_project = self.of_dao.get_project_by_id(of_project_id)

            delete_az_story = False
            az_story_is_completed = az_story.phase.id in (
                az_phases.done.id, az_phases.archive.id)
            az_story_is_in_progress = az_story.phase.id not in (
                az_phases.backlog.id, az_phases.ready.id,
                az_phases.done.id, az_phases.archive.id)

            # Delete AZ stories that no more correspond to a selected
            # project in OF, except if the OF project still exists and
            # either the OF project or AZ story is completed, to keep
            # a trace of completed projects in AZ until they are
            # deleted (e.g. archived) in OF.
            delete_az_story = (
                of_project is None
                or of_project_id not in of_project_ids
                or of_project.status == appscript.k.dropped)

            if delete_az_story:
                LOG.debug('deleting AgileZen story %s "%s"',
                          az_story.id, az_story.text)
                self.az_dao.delete_project_story(az_project.id, az_story.id)
            else:
                # Update the OmniFocus project.  The only update that
                # can be performed on an OmniFocus project is setting
                # it as active or completed, in case the AgileZen task
                # is in an active or completed phase.
                # The philosophy is that a project / story can only
                # progress forward, never backward, so always in the
                # order: backlog -> ready -> ... -> done & archive.
                # Which ever of OmniFocus or AgileZen makes a project
                # / story go forward has precedence on the other re:
                # the status.
                if (az_story_is_in_progress
                    and of_project.status == appscript.k.on_hold):
                    LOG.debug(
                        'marking as active OmniFocus project %s "%s"',
                        of_project.id, of_project.name)
                    self.of_dao.set_project_active(of_project)
                elif az_story_is_completed and not of_project.completed:
                    LOG.debug(
                        'marking as completed OmniFocus project %s "%s"',
                        of_project.id, of_project.name)
                    self.of_dao.set_project_completed(of_project)

                # Update the AgileZen story if either the AZ story or
                # the OF project has been modified.  Such updates
                # always flow from OF to AZ, never the other way
                # round: OF is the golden standard.
                # Ignore the current story's owner if the owner option
                # is not set, i.e. owner is None.
                updated_text = self._get_az_story_text_for_project(of_project)
                updated_details = self._get_az_story_details_for_project(
                    of_project)
                updated_color = of_color_picker(of_project)
                updated_phase = self._get_az_story_phase_for_project(
                    of_project, az_phases, az_story.phase)
                if (az_story.text != updated_text or
                    az_story.details != updated_details or
                    az_story.color != updated_color or
                    az_story.phase.id != updated_phase.id or
                    owner is not None and (
                        az_story.owner is None or
                        az_story.owner.userName != owner.userName)):
                    LOG.debug('updating AgileZen story %s "%s"',
                              az_story.id, az_story.text)
                    self.az_dao.update_project_story(
                        az_project.id,
                        az_story._replace(
                            text=updated_text,
                            details=updated_details,
                            color=updated_color,
                            phase=updated_phase,
                            owner=owner))

                # Update the AgileZen story's tags.
                updated_tags = self._get_az_tags_for_project(of_project)
                all_used_tags.update(updated_tags)
                if (set([tag.name for tag in az_story.tags])
                        != set([tag.name for tag in updated_tags])):
                    LOG.debug('updating AgileZen tags in story %s "%s"',
                              az_story.id, az_story.text)
                    self.az_dao.update_project_story_tags(
                        az_project.id, az_story.id, updated_tags)

                # Update the tasks in the AgileZen story if any AZ
                # task or OF task has been added, deleted, or
                # modified.  OF is the golden standard for tasks.
                # Task updates always flow from OF to AZ, never the
                # other way round, except for completion status: if a
                # task is marked as completed in either AZ or OF, it
                # is then marked as completed in the other.  After
                # sync, each AZ story only contains non-completed
                # tasks, and completed tasks are deleted in AZ and
                # marked as completed in OF.

                # First update the set of tasks, regardless of their
                # order.

                # The current dict of all (completed or not) tasks in
                # the OF project.
                of_tasks_dict = dict(
                    [(task.name, task) for task in of_project.root_task.tasks])
                # The current list of (completed or not) tasks in the
                # AZ story, with AZ task IDs, etc.
                az_tasks_cur = az_story.tasks
                az_tasks_cur_dict = dict(
                    [(task.text, task) for task in az_tasks_cur])
                az_tasks_cur_texts = set(az_tasks_cur_dict.iterkeys())

                # The list of AZ tasks reflecting the list of tasks in
                # the OF project, both completed and non-completed.
                # This is the desired list of tasks in the AZ story.
                # If a story has been set as completed in AZ, set it
                # also as completed in the target list.  The code
                # below updates the AZ story to contain exactly this
                # list.
                az_tasks_new = [
                    task._replace(status=True)
                        if not task.status
                            and az_tasks_cur_dict.has_key(task.text)
                            and az_tasks_cur_dict[task.text].status
                        else task
                    for task in self._get_az_tasks_for_project(of_project)]
                az_tasks_new_dict = dict(
                    [(task.text, task) for task in az_tasks_new])
                az_tasks_new_texts = set(az_tasks_new_dict.iterkeys())

                # Delete tasks in AZ if they are deleted in OF.
                for az_task_text in az_tasks_cur_texts - az_tasks_new_texts:
                    az_task = az_tasks_cur_dict[az_task_text]
                    LOG.debug(
                        'deleting AgileZen task %s "%s" in story %s "%s"',
                        az_task.id, az_task.text, az_story.id, az_story.text)
                    self.az_dao.delete_project_story_task(
                        az_project.id, az_story.id, az_task.id)
                    del az_tasks_cur_dict[az_task.text]
                    az_tasks_cur.remove(az_task)
                # Add new tasks.
                for az_task_text in az_tasks_new_texts - az_tasks_cur_texts:
                    az_task = az_tasks_new_dict[az_task_text]
                    LOG.debug(
                        'creating AgileZen task "%s" in story %s "%s"',
                        az_task.text, az_story.id, az_story.text)
                    created_az_task = self.az_dao.create_project_story_task(
                        az_project.id, az_story.id, az_task)
                    az_tasks_cur_dict[created_az_task.text] = created_az_task
                    # Tasks newly created via the API in AgileZen are
                    # inserted first.
                    az_tasks_cur.insert(0, created_az_task)

                # Update the status of tasks, either in AgileZen or
                # OmniFocus.  If a task on any side is complete, the
                # other is also marked as complete.
                for az_task_new in az_tasks_new:
                    az_task_cur = az_tasks_cur_dict[az_task_new.text]
                    if az_task_new.status and not az_task_cur.status:
                        # Mark the task as completed in AZ.
                        LOG.debug('marking as completed AgileZen task "%s" '
                                  'in story %s "%s"',
                                  az_task_new.text, az_story.id, az_story.text)
                        az_task_new = az_task_cur._replace(status=True)
                        self.az_dao.update_project_story_task(
                            az_project.id, az_story.id, az_task_new)
                    elif az_task_cur.status:
                        # Mark the tasks as completed in OF.
                        of_task = of_tasks_dict.get(az_task_new.text)
                        if of_task is not None and not of_task.completed:
                            LOG.debug(
                                'marking as completed OmniFocus task %s "%s" '
                                'in project %s "%s"', of_task.id, of_task.name,
                                of_project.id, of_project.name)
                            self.of_dao.set_task_completed(of_task)
                        
                # Second, reorder tasks.
                az_tasks_cur_ord_texts = [task.text for task in az_tasks_cur]
                az_tasks_new_ord_texts = [task.text for task in az_tasks_new]
                if az_tasks_cur_ord_texts != az_tasks_new_ord_texts:
                    # Reorder tasks.
                    LOG.debug(
                        'reordering AgileZen tasks in story %s "%s"',
                        az_story.id, az_story.text)
                    # Use the IDs of previously or newly created AZ
                    # Task objects, following the order of Task
                    # objects in az_tasks_new.
                    import json
                    self.az_dao.reorder_project_story_tasks(
                        az_project.id, az_story.id,
                        [az_tasks_cur_dict[az_task.text].id
                         for az_task in az_tasks_new])

        # Delete tags that are now unused, after having dissociated
        # them from AZ stories.
        all_tags = self.az_dao.iter_project_tags(az_project_id)
        all_used_tag_names = set([tag.name for tag in all_used_tags])
        for tag in all_tags:
            if tag.name not in all_used_tag_names:
                LOG.debug('deleting AgileZen tag %i "%s"', tag.id, tag.name)
                self.az_dao.delete_project_tag(az_project.id, tag.id)


def main():
    default_api_key_file = os.path.expanduser('~/.agilezenapikey')

    # TODO: Get the project name, version number, copyright, and
    # contact information from configure.
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description='Synchronize OmniFocus with AgileZen',
        prog='omnifocus2agilezen',
        version='''Pikpoint 0.1~pre1
Copyright (C) 2012 Romain Lenglet
License AGPLv3+:
GNU AGPL version 3 or later <http://gnu.org/licenses/agpl-3.0.html>
This is free software: you are free to change and redistribute it.
There is NO WARRANTY, to the extent permitted by law.''',
        epilog='''Report bugs to: Romain Lenglet <romain.lenglet@berabera.info>
Pikpoint home page: <https://github.com/rlenglet/pikpoint>''')

    parser.add_argument(
        '-k', '--api-key-file', default=default_api_key_file,
        help='the file containing an AgileZen API key on the first line '
             '(default: %(default)s); '
             'see http://dev.agilezen.com/concepts/authentication.html '
             'to create an API key',
        metavar='FILE')

    parser.add_argument(
        '-p', '--project', required=True, type=int,
        help='the ID of the AgileZen project to sync to',
        metavar='ID')

    parser.add_argument(
        '-d', '--due-soon', default=DUE_SOON_DAYS, type=int,
        help='the number of days in the future whithin which deadlines are '
             'considered "due soon" (default: %(default)i)',
        metavar='DAYS')

    parser.add_argument(
        '-o', '--owner',
        help='the username of the owner to assign to all AgileZen stories '
             '(default: no owner assigned)',
        metavar='USERNAME')

    troubleshooting_group = parser.add_argument_group(
        'optional troubleshooting arguments',
        'options not intended for general use')

    troubleshooting_group.add_argument(
        '-V', '--verbose', action='store_true',
        help='turn on verbose debugging logging to the standard output '
             '(default: off)')

    troubleshooting_group.add_argument(
        '--api-base-url', default=AGILEZEN_API_BASE_URL,
        help='the base URL of the AgileZen API (default: %(default)s)',
        metavar='URL')

    troubleshooting_group.add_argument(
        '--disable-verify-ssl-cert', action='store_true',
        help='disables verifying the SSL certificate of the AgileZen '
             'API server (default: enabled)')

    options = parser.parse_args()

    if options.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.WARNING)

    az_project_id = options.project
    az_api_key = None
    with open(options.api_key_file) as f:
        for line in f:
            az_api_key = line.rstrip('\n\r')
            break
    if not az_api_key:
        LOG.error('no API key found in file "%s"', options.api_key_file)
        raise ValueError('invalid key file "%s"' % (options.api_key_file,))
    verify_ssl_cert = not options.disable_verify_ssl_cert

    LOG.info('syncing to AgileZen project ID %i', az_project_id)
    LOG.debug('syncing to URL "%s" using AgileZen API key "%s"',
              options.api_base_url, az_api_key)
    LOG.debug('projects are due soon in %i days', options.due_soon)

    start_time = datetime.datetime.now()

    omnifocus_app = appscript.app(name='OmniFocus')
    if not omnifocus_app.isrunning():
        LOG.error('OmniFocus is not running')
        raise IOError('OmniFocus is not running')
    omnifocus_dao = omnifocus.OmniFocusDataAccess(omnifocus_app)

    agilezen_dao = agilezen.AgileZenDataAccess(
        options.api_base_url, az_api_key, page_size=100,
        verify_ssl_cert=verify_ssl_cert)

    sync = OmniFocusToAgileZenSync(omnifocus_dao, agilezen_dao,
                                   due_soon_days=options.due_soon)
    sync.sync_projects(
        # Ignore projects on-hold, "action bags", or not yet scheduled.
        # lambda proj: proj.status == appscript.k.active
        #              and not proj.singleton_action_holder
        #              and (proj.start_date is None
        #                   or proj.start_date < datetime.datetime.now()),
        # Ignore "action bags", or projects not yet scheduled.
        lambda proj: proj.status != appscript.k.dropped
                     and not proj.singleton_action_holder
                     and (proj.start_date is None
                          or proj.start_date < datetime.datetime.now()),
        lambda proj: 'blue' if proj.full_context_name.startswith('VMware')
                            else 'green',
        az_project_id, owner_username=options.owner)

    end_time = datetime.datetime.now()
    LOG.debug('sync completed in %s', end_time - start_time)


if __name__ == '__main__':
    main()
