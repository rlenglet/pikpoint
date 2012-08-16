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

AC_PREREQ(2.64)
AC_INIT(Pikpoint, 0.1~pre1, romain.lenglet@berabera.info, pikpoint)
AC_COPYRIGHT([Copyright 2012 Romain Lenglet])
AC_CONFIG_AUX_DIR([config])
AC_CONFIG_MACRO_DIR([config])

AM_INIT_AUTOMAKE([silent-rules])

AM_PATH_PYTHON

AX_PYTHON_CHECK_MODULE_IMPORT([appscript], [],
  [AC_MSG_ERROR([the appscript Python library is required])])
AX_PYTHON_CHECK_MODULE_IMPORT([requests], [],
  [AC_MSG_ERROR([the Requests Python library is required])])

AC_CONFIG_FILES([Makefile
src/Makefile])

AC_OUTPUT
