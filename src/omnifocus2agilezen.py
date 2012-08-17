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


class OmniFocusToAgileZenSync(object):
    """A synchronizer between OmniFocus and AgileZen.
    """

    def __init__(self, omnifocus_dao, agilezen_dao):
        """Initialize this synchronizer with the OF and AZ DAOs.

        Args:
            omnifocus_dao: The OmniFocusDataAccess object to use to
                access the OmniFocus database.

            agilezen_dao: The AgileZenDataAccess object to use to
                access the AgileZen database.
        """
        self.of_dao = omnifocus_dao
        self.az_dao = agilezen_dao

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

    @classmethod
    def _get_az_story_text_for_project(cls, of_project):
        """Gets an AgileZen story's text from an OmniFocus project.

        Args:
            of_project: The OmniFocus project to get information from.

        Returns:
            An AgileZen story's text containing information about the
            OmniFocus project.
        """
        return '\n'.join(
            ['**%s**' % (of_project.name,),
             '%s' % (of_project.full_folder_name,),
             '*%s*' % (of_project.full_context_name,),
             ])

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
            return az_phases.backlog
        elif of_project.completed:
            if current_az_phase.id in (az_phases.done.id, az_phases.archive.id):
                # Already completed too.
                return current_az_phase
            else:
                return az_phases.done
        else:
            if current_az_phase.id == az_phases.backlog.id:
                return az_phases.ready
            else:
                return current_az_phase

    @staticmethod
    def _get_az_tasks_for_project(of_project):
        """Gets a list of AgileZen tasks from an OmniFocus project's tasks.

        Args:
            of_project: The OmniFocus project to get tasks from.

        Returns:
            The list of Task objects corresponding to the OmniFocus
            project's non-completed tasks.
        """
        # Task text is plain text, not Markdown, so we can't hide any
        # OmniFocus task ID in there.  Rely on text comparisons to
        # synchronize tasks.  Don't include tasks completed in
        # OmniFocus, so that completed tasks are deleted from AgileZen
        # stories during synchronization.
        return [agilezen.Task(None, task.name, None, None, None, False)
            for task in of_project.root_task.tasks
            if not task.completed]

    def sync_projects(self, of_project_selector, of_color_picker,
                      az_project_name):
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
            az_project_name: The name of the AgileZen project to
                contain the stories.
        """
        # Find the project with the given name.
        az_project = None
        for proj in self.az_dao.iter_projects(
            where='name:%s' % (az_project_name,)):
            az_project = proj
        assert az_project is not None

        az_phases = agilezen.ProjectPhases.parse_phases(
            list(self.az_dao.iter_project_phases(az_project.id)))

        of_projects_dict = self.of_dao.get_projects(of_project_selector)
        az_stories = list(self.az_dao.iter_project_stories(
                az_project.id, with_details=True, with_tasks=True))
        # Delete stories that have no OmniFocus project ID.
        for az_story in az_stories:
            if self._get_omnifocus_id(az_story) is None:
                logging.debug('deleting AgileZen story %s "%s"',
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
                                                     az_phases.ready),
                None,
                None,
                None,
                self._get_az_tasks_for_project(of_project))
            logging.debug('creating AgileZen story "%s"', az_story.text)
            self.az_dao.create_project_story(az_project.id, az_story)

        # TODO: Copy the project's "estimated_minutes" into the
        # story's size.

        for of_project_id in az_of_project_ids:
            az_story = az_stories_dict[of_project_id]
            of_project = self.of_dao.get_project_by_id(of_project_id)

            delete_az_story = False
            az_story_is_in_backlog = az_story.phase.id == az_phases.backlog.id
            az_story_is_completed = az_story.phase.id in (
                az_phases.done.id, az_phases.archive.id)
            az_story_is_active = not (az_story_is_in_backlog
                                      or az_story_is_completed)

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
                logging.debug('deleting AgileZen story %s "%s"',
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
                if (az_story_is_active
                    and of_project.status == appscript.k.on_hold):
                    logging.debug(
                        'marking as active OmniFocus project %s "%s"',
                        of_project.id, of_project.name)
                    self.of_dao.set_project_active(of_project)
                elif az_story_is_completed and not of_project.completed:
                    logging.debug(
                        'marking as completed OmniFocus project %s "%s"',
                        of_project.id, of_project.name)
                    self.of_dao.set_project_completed(of_project)

                # Update the AgileZen story if either the AZ story or
                # the OF project has been modified.  Such updates
                # always flow from OF to AZ, never the other way
                # round: OF is the golden standard.
                updated_text = self._get_az_story_text_for_project(of_project)
                updated_details = self._get_az_story_details_for_project(
                    of_project)
                updated_color = of_color_picker(of_project)
                updated_phase = self._get_az_story_phase_for_project(
                    of_project, az_phases, az_story.phase)
                if (az_story.text != updated_text or
                    az_story.details != updated_details or
                    az_story.color != updated_color or
                    az_story.phase.id != updated_phase.id):
                    logging.debug('updating AgileZen story %s "%s"',
                                  az_story.id, az_story.text)
                    self.az_dao.update_project_story(
                        az_project.id,
                        az_story._replace(
                            text=updated_text,
                            details=updated_details,
                            color=updated_color,
                            phase=updated_phase))

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
                # The current list of (completed or not) tasks in the AZ story.
                az_tasks_old = az_story.tasks

                # Mark tasks as completed in OF if they are completed
                # in AZ.
                for az_task in az_tasks_old:
                    if az_task.status:  # AZ task is completed.
                        of_task = of_tasks_dict.get(az_task.text)
                        if of_task is not None and not of_task.completed:
                            logging.debug(
                                'marking as completed OmniFocus task %s "%s" '
                                'in project %s "%s"', of_task.id, of_task.name,
                                of_project.id, of_project.name)
                            self.of_dao.set_task_completed(of_task)

                # The list of currently non-completed tasks,
                # calculated from the OF project.  This is the desired
                # list of tasks in the AZ story.  The code below
                # updates the AZ story to contain exactly this list.
                az_tasks_new = self._get_az_tasks_for_project(of_project)

                az_tasks_old_dict = dict(
                    [(task.text, task) for task in az_tasks_old])
                az_tasks_with_ids_dict = az_tasks_old_dict.copy()
                az_tasks_new_dict = dict(
                    [(task.text, task) for task in az_tasks_new])
                az_tasks_old_texts = set(az_tasks_old_dict.iterkeys())
                az_tasks_new_texts = set(az_tasks_new_dict.iterkeys())

                # Delete tasks in AZ if they are completed in OF.
                for az_task_text in az_tasks_old_texts - az_tasks_new_texts:
                    az_task = az_tasks_old_dict[az_task_text]
                    logging.debug(
                        'deleting AgileZen task %s "%s" in story %s "%s"',
                        az_task.id, az_task.text, az_story.id, az_story.text)
                    self.az_dao.delete_project_story_task(
                        az_project.id, az_story.id, az_task.id)
                    del az_tasks_with_ids_dict[az_task.text]
                # Add new tasks.
                for az_task_text in az_tasks_new_texts - az_tasks_old_texts:
                    az_task = az_tasks_new_dict[az_task_text]
                    logging.debug(
                        'creating AgileZen task "%s" in story %s "%s"',
                        az_task.text, az_story.id, az_story.text)
                    created_az_task = self.az_dao.create_project_story_task(
                        az_project.id, az_story.id, az_task)
                    az_tasks_with_ids_dict[created_az_task.text] = \
                        created_az_task
                # Reorder tasks.
                az_tasks_old_ord_texts = [task.text for task in az_tasks_old]
                az_tasks_new_ord_texts = [task.text for task in az_tasks_new]
                # TODO: Reduce the cases where we need to reorder.
                if az_tasks_old_ord_texts != az_tasks_new_ord_texts:
                    # Reorder tasks.
                    logging.debug(
                        'reordering AgileZen tasks in story %s "%s"',
                        az_story.id, az_story.text)
                    # Use the newly created AZ Task objects's IDs,
                    # following the order of partial Task objects in
                    # az_tasks_new.
                    self.az_dao.reorder_project_story_tasks(
                        az_project.id, az_story.id,
                        [az_tasks_with_ids_dict[az_task.text].id
                         for az_task in az_tasks_new])


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
             'see <http://dev.agilezen.com/concepts/authentication.html> '
             'to create an API key',
        metavar='FILE')

    parser.add_argument(
        '-p', '--project', required=True,
        help='the AgileZen project name to sync to')

    parser.add_argument(
        '-V', '--verbose', action='store_true',
        help='turn on verbose debugging logging to the standard output '
             '(default: off)')

    options = parser.parse_args()

    if options.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.WARNING)

    az_project = options.project
    az_api_key = None
    with open(options.api_key_file) as f:
        for line in f:
            az_api_key = line.rstrip('\n\r')
            break
    assert az_api_key is not None

    logging.info('syncing to AgileZen project "%s"', az_project)
    logging.debug('using AgileZen API key "%s"', az_api_key)

    start_time = datetime.datetime.now()

    omnifocus_app = appscript.app(name='OmniFocus')
    assert omnifocus_app.isrunning()
    omnifocus_dao = omnifocus.OmniFocusDataAccess(omnifocus_app)

    agilezen_dao = agilezen.AgileZenDataAccess(
        AGILEZEN_API_BASE_URL, az_api_key, page_size=100)

    sync = OmniFocusToAgileZenSync(omnifocus_dao, agilezen_dao)
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
        az_project)

    end_time = datetime.datetime.now()
    logging.debug('sync completed in %s', end_time - start_time)


if __name__ == '__main__':
    main()
