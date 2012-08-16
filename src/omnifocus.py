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


# appscript
# URL: http://appscript.sourceforge.net/
# MacPorts package: py*-appscript
import appscript


class LazyAppScriptObject(object):
    """A proxy to an AppScript object that caches objects and attributes.
    """

    def __init__(self, raw_obj, proxy_cache):
        """Initialize this proxy to proxy the given AppScript object.

        Args:
            raw_obj: The AppScript object to proxy.
            proxy_cache: The dictionary to use as a proxy object cache
                when reading object attributes.  Keys are object IDs.
        """
        self.__dict__['_raw_obj'] = raw_obj
        self.__dict__['_proxy_cache'] = proxy_cache

    def _convert_attr_value(self, v):
        """Converts an AppScript attribute value.

        Args:
            v: The AppScript attribut value to convert.

        Returns:
            The converted or proxied attribute value.
        """
        if v == appscript.k.missing_value:
            return None
        elif isinstance(v, appscript.Reference):
            proxy_cache = self.__dict__['_proxy_cache']
            id = v.id
            proxy = proxy_cache.get(id)
            if proxy is None:
                proxy = self.__class__(v, proxy_cache)
                proxy_cache[id] = proxy
            return proxy
        elif isinstance(v, list):
            return [self._convert_attr_value(o) for o in v]
        else:
            return v

    def _get_app_attr(self, name):
        """Gets an attribute's value from the proxied AppScript object.

        This method can be redefined in subclasses to convert values
        to more user-friendly types.

        Args:
            name: The attribute's name.

        Returns:
            The value of the attribute from the proxied AppScript
            object, or None if it has no value.
        """
        return self._convert_attr_value(getattr(self._raw_obj, name).get())

    def __getattr__(self, name):
        """Gets and caches an attribute's value.

        The attribute's value is retrieved from the proxied AppScript
        object and cached, so that the next accesses to the attribute
        will hit the cache.

        Args:
            name: The attribute's name.

        Returns:
            The value of the attribute from the proxied AppScript object.
        """
        value = self._get_app_attr(name)
        self.__dict__[name] = value
        return value

    def __setattr__(self, name, value):
        """Sets an attribute's value.

        The attribute's value is modified both in the cache and in the
        proxied AppScript object (write-through).

        Args:
            name: The attribute's name.
            value: The attribute's value.
        """
        getattr(self._raw_obj, name).set(value)
        self.__dict__[name] = value


class OmniFocusLazyAppScriptObject(LazyAppScriptObject):
    """A proxy with special handling of OmniFocus-specific attributes.
    """

    def _get_app_attr(self, name):
        if name == 'full_context_name':
            name_parts = []
            has_parent = True
            context = self.context
            while has_parent and context is not None:
                name_parts.insert(0, context.name)
                parent = context.container
                has_parent = (parent != context.containing_document)
                context = parent
            return '/'.join(name_parts)
        elif name == 'full_folder_name':
            name_parts = []
            has_parent = True
            folder = self.container
            while folder != self.containing_document:
                name_parts.insert(0, folder.name)
                folder = folder.container
            return ', '.join(name_parts)
        else:
            return LazyAppScriptObject._get_app_attr(self, name)


class OmniFocusDataAccess(object):
    """Provides access to OmniFocus projects and tasks.

    Most methods provide only read-only access, to minimize the risks
    of corruption of OmniFocus's database.
    """

    def __init__(self, app):
        """Initialize this DAO to the given AppleScript application stub.

        Args:
            app: The appscript app object to use to access
                OmniFocus. The application must be running.
        """
        self.app = app
        self.obj_cache = dict()

    def _proxy_object(self, raw_obj):
        """Create a caching proxy object to proxy an AppScript object.

        Caching proxy objects are cached, so successive calls for the
        same task ID will return the same proxy object.

        Args:
            raw_obj: The AppScript object to wrap into a caching proxy
                object.

        Returns:
            A cached or a newly created caching proxy object with the
            ID in the given raw_obj.
        """
        obj_id = raw_obj.id.get()
        proxy = self.obj_cache.get(obj_id)
        if proxy is None:
            proxy = OmniFocusLazyAppScriptObject(raw_obj, self.obj_cache)
            self.obj_cache[obj_id] = proxy
        return proxy

    def get_project_by_id(self, project_id):
        """Get a single project given its ID.

        Args:
            project_id: The ID of the project to retrieve.

        Returns:
            The project with the given ID, or None if not found.
        """
        try:
            project = self.obj_cache.get(project_id)
            if project is None:
                raw_project = self.app.default_document.projects.ID(project_id)
                project = self._proxy_object(raw_project)
            return project
        except appscript.reference.CommandError, e:
            return None  # Not found.

    def get_task_by_id(self, task_id):
        """Get a single task given its ID.

        Args:
            task_id: The ID of the task to retrieve.

        Returns:
            The task with the given ID, or None if not found.
        """
        try:
            task = self.obj_cache.get(task_id)
            if task is None:
                raw_task = self.app.default_document.tasks.ID(task_id)
                task = self._proxy_object(raw_task)
            return task
        except appscript.reference.CommandError, e:
            return None  # Not found.

    def get_projects(self, selector):
        """Get all projects.

        Args:
            selector: A callable taking a project object, and returns
                True or False whether the project must be selected or
                not.

        Returns:
            A dict which keys are project IDs and values are tuples
            (index, project) where index reflect the relative order of
            projects in the results, and project is a project object.
        """
        raw_projects = self.app.default_document.flattened_projects.get()
        projects = [self._proxy_object(project) for project in raw_projects]
        selected_projects = [project for project in projects
                             if selector(project)]
        indexed_projects = zip(xrange(0, len(selected_projects)),
                               selected_projects)
        return dict([(index_project[1].id, index_project)
                     for index_project in indexed_projects])

    def get_next_tasks(self, selector):
        """Get all next tasks.

        Args:
            selector: A callable taking a task object, and returns
                True or False whether the task must be selected or
                not.

        Returns:
            A dict which keys are task IDs and values are tuples
            (index, task) where index reflect the relative order of
            tasks in the results, and task is a next-action task object.
        """
        raw_tasks = self.app.default_document.flattened_tasks[
            (appscript.its.blocked == False).AND
            (appscript.its.completed == False).AND
            (appscript.its.containing_project.status == appscript.k.active).AND
            ((appscript.its.containing_project.next_task ==
              appscript.k.missing_value).OR
             (appscript.its.containing_project.next_task == appscript.its))
            ].get()
        next_tasks = [self._proxy_object(task) for task in raw_tasks]
        selected_tasks = [task for task in next_tasks if selector(task)]
        indexed_tasks = zip(xrange(0, len(selected_tasks)), selected_tasks)
        return dict([(index_task[1].id, index_task)
                     for index_task in indexed_tasks])

    def set_project_completed(self, project):
        """Set a project as completed.

        Args:
            project: The project to mark as completed, as a project
                object.
        """
        if not project.completed:
            project.completed = True

    def set_project_active(self, project):
        """Set a project as active.

        Args:
            project: The project to mark as active, as a project
                object.
        """
        if project.status != appscript.k.active:
            project.status = appscript.k.active

    def set_task_completed(self, task):
        """Set a task as completed.

        Args:
            task: The task to mark as completed, as a task object.
        """
        if not task.completed:
            task.completed = True
