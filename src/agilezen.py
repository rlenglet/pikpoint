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


import collections
import datetime
import json
import logging

# Requests
# URL: http://docs.python-requests.org/
# MacPorts package: py*-requests py*-certifi
import requests


TIME_FORMAT = '%Y-%m-%dT%H:%M:%S'

LOG = logging.getLogger('agilezen')


class JsonSerializable(object):

    def to_json(self):
        return dict([(field, self._field_to_json(field))
                     for field in self._fields
                     if getattr(self, field) is not None])

    @classmethod
    def create_from_json(cls, json_obj):
        args = [
            (cls._json_to_field(field, json_obj[field])
             if field in json_obj else None)
            for field in cls._fields]
        return cls(*args)

    def _field_to_json(self, field):
        return getattr(self, field)

    @classmethod
    def _json_to_field(cls, field, json_value):
        return json_value


class User(collections.namedtuple('User', ('id', 'email', 'name', 'userName')),
           JsonSerializable):
    pass


class Project(collections.namedtuple('Project',
                                     ('id', 'createTime', 'description', 'name',
                                      'owner')),
              JsonSerializable):

    def _field_to_json(self, field):
        if field == 'createTime':
            return self.createTime.strftime(TIME_FORMAT)
        elif field == 'owner':
            return self.owner.to_json()
        else:
            return getattr(self, field)

    @classmethod
    def _json_to_field(cls, field, json_value):
        if field == 'createTime':
            return datetime.datetime.strptime(json_value[:19], TIME_FORMAT)
        elif field == 'owner':
            return User.create_from_json(json_value)
        else:
            return json_value


class Phase(collections.namedtuple('Phase',
                                   ('id', 'name', 'description', 'index',
                                    'limit')),
            JsonSerializable):
    pass


class ProjectPhases(collections.namedtuple('ProjectPhases', (
            'backlog', 'ready', 'first_in_progress', 'done', 'archive'))):

    @classmethod
    def parse_phases(cls, phases):
        """Gets the set of key phases in a project.

        The first phase after the backlog is selected as the "ready"
        phase, i.e. the initial phase of new stories.  The
        next-to-last phase is selected as the "done" phse, i.e. the
        phase for completed stories.

        Args:
            phases: The list of Phase objects in a project.
        """
        backlog = None
        ready = None
        done = None
        archive = None
        for phase in phases:
            if phase.index == 0:
                backlog = phase
            elif phase.index == 1:
                ready = phase
            elif phase.index == 2:
                first_in_progress = phase
            elif phase.index == len(phases) - 2:
                done = phase
            elif phase.index == len(phases) - 1:
                archive = phase
        if backlog is None:
            LOG.error('no "backlog" phase found')
            raise ValueError('no "backlog" phase found')
        if ready is None:
            LOG.error('no "ready" phase found')
            raise ValueError('no "ready" phase found')
        if first_in_progress is None:
            LOG.error('no "in progress" phase found')
            raise ValueError('no "in progress" phase found')
        if done is None:
            LOG.error('no "done" phase found')
            raise ValueError('no "done" phase found')
        if archive is None:
            LOG.error('no "archive" phase found')
            raise ValueError('no "archive" phase found')
        return cls(backlog, ready, first_in_progress, done, archive)


class Tag(collections.namedtuple('Tag', ('id', 'name')),
          JsonSerializable):
    pass


class Task(collections.namedtuple('Task',
                                  ('id', 'text', 'createTime', 'finishTime',
                                   'finishedBy', 'status')),
           JsonSerializable):

    def _field_to_json(self, field):
        if field == 'status':
            return 'complete' if self.status else 'incomplete'
        if field in ('createTime', 'finishTime'):
            return getattr(self, field).strftime(TIME_FORMAT)
        elif field == 'finishedBy':
            return self.finishedBy.to_json()
        else:
            return getattr(self, field)

    @classmethod
    def _json_to_field(cls, field, json_value):
        if field == 'status':
            return json_value == 'complete'
        if field in ('createTime', 'finishTime'):
            return datetime.datetime.strptime(json_value[:19], TIME_FORMAT)
        elif field == 'finishedBy':
            return User.create_from_json(json_value)
        else:
            return json_value


# Valid colors for stories.
COLORS = ['grey', 'blue', 'red', 'green', 'orange', 'yellow', 'purple', 'teal']


class Story(collections.namedtuple('Story',
                                   ('id', 'text', 'details', 'size', 'priority',
                                    'color', 'phase', 'creator', 'owner',
                                    'tags', 'tasks')),
            JsonSerializable):

    def _field_to_json(self, field):
        if field in ('phase', 'creator', 'owner'):
            return getattr(self, field).to_json()
        elif field in ('tags', 'tasks'):
            return [obj.to_json() for obj in getattr(self, field)]
        else:
            return getattr(self, field)

    @classmethod
    def _json_to_field(cls, field, json_value):
        if field == 'phase':
            return Phase.create_from_json(json_value)
        if field in ('creator', 'owner'):
            return User.create_from_json(json_value)
        if field == 'tags':
            return [Tag.create_from_json(tag) for tag in json_value]
        if field == 'tasks':
            return [Task.create_from_json(task) for task in json_value]
        else:
            return json_value


class AgileZenDataAccess(object):
    """Provides access to AgileZen projects, stories, tasks, etc.
    """

    def __init__(self, api_base_url, api_key, page_size=100,
                 verify_ssl_cert=True):
        self.api_base_url = api_base_url
        self.api_key = api_key
        self.page_size = page_size
        self.session = requests.session(verify=verify_ssl_cert)

    def _get_headers(self):
        return {
            'X-Zen-ApiKey': self.api_key,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            }

    def _get(self, path, params=None):
        url = self.api_base_url + path
        response = self.session.get(url, params=params,
                                    headers=self._get_headers())
        if response.status_code != 200:
            LOG.error('HTTP request failed with status code %i',
                         response.status_code)
            raise IOError('HTTP request failed')
        return response.json

    def _post(self, path, data):
        url = self.api_base_url + path
        if data is not None:
            data = json.dumps(data)
        response = self.session.post(url, data=data,
                                     headers=self._get_headers())
        if response.status_code != 200:
            LOG.error('HTTP request failed with status code %i',
                         response.status_code)
            raise IOError('HTTP request failed')
        return response.json

    def _put(self, path, data):
        url = self.api_base_url + path
        if data is not None:
            data = json.dumps(data)
        response = self.session.put(url, data=data,
                                    headers=self._get_headers())
        if response.status_code != 200:
            LOG.error('HTTP request failed with status code %i',
                         response.status_code)
            raise IOError('HTTP request failed')
        return response.json

    def _delete(self, path, params=None):
        url = self.api_base_url + path
        response = self.session.delete(url, params=params,
                                       headers=self._get_headers())
        if response.status_code != 200:
            LOG.error('HTTP request failed with status code %i',
                         response.status_code)
            raise IOError('HTTP request failed')

    def _iter_query(self, path, add_params=None):
        page = 1
        while True:
            params = {
                'page': page,
                'pageSize': self.page_size,
                }
            if add_params:
                params.update(add_params)
            # TODO: Support adding filters.
            query_res = self._get(path, params=params)
            for json_obj in query_res['items']:
                yield json_obj
            if page >= query_res['totalPages']:
                return
            page += 1

    def iter_projects(self, where=None):
        add_params = {}
        if where is not None:
            add_params['where'] = where
        for json_obj in self._iter_query('projects', add_params=add_params):
            yield Project.create_from_json(json_obj)

    def get_project(self, project_id):
        return Project.create_from_json(
            self._get('/'.join(['projects', str(project_id)])))

    def iter_project_phases(self, project_id):
        for json_obj in self._iter_query(
            '/'.join(['projects', str(project_id), 'phases'])):
            yield Phase.create_from_json(json_obj)

    def iter_project_stories(self, project_id, with_details=False,
                             with_tags=False, with_tasks=False):
        # TODO: Support adding filters.
        enrichments = []
        if with_details:
            enrichments.append('details')
        if with_tags:
            enrichments.append('tags')
        if with_tasks:
            enrichments.append('tasks')
        add_params = {'with': ','.join(enrichments)} if enrichments else {}
        for json_obj in self._iter_query(
            '/'.join(['projects', str(project_id), 'stories']),
            add_params=add_params):
            yield Story.create_from_json(json_obj)

    def create_project_story(self, project_id, story):
        return Story.create_from_json(
            self._post(
                '/'.join(['projects', str(project_id), 'stories']),
                data=story.to_json()))

    def create_project_story_task(self, project_id, story_id, task):
        return Task.create_from_json(
            self._post(
                '/'.join(['projects', str(project_id),
                          'stories', str(story_id),
                          'tasks']),
                data=task.to_json()))

    def update_project_story(self, project_id, story):
        return Story.create_from_json(
            self._put(
                '/'.join(['projects', str(project_id),
                          'stories', str(story.id)]),
                data=story.to_json()))

    def update_project_story_task(self, project_id, story_id, task):
        return Task.create_from_json(
            self._put(
                '/'.join(['projects', str(project_id),
                          'stories', str(story_id),
                          'tasks', str(task.id)]),
                data=task.to_json()))

    def reorder_project_story_tasks(self, project_id, story_id, task_ids):
        json_tasks = self._put(
                '/'.join(['projects', str(project_id),
                          'stories', str(story_id),
                          'tasks']),
                data=task_ids)
        return [Task.create_from_json(json_task) for json_task in json_tasks]

    def delete_project_story(self, project_id, story_id):
        self._delete('/'.join(['projects', str(project_id),
                               'stories', str(story_id)]))

    def delete_project_story_task(self, project_id, story_id, task_id):
        self._delete('/'.join(['projects', str(project_id),
                               'stories', str(story_id),
                               'tasks', str(task_id)]))
