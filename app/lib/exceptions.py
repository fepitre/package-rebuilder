#
# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2021 Frédéric Pierret (fepitre) <frederic.pierret@qubes-os.org>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#


class RebuilderException(Exception):
    pass


class RebuilderExceptionDist(RebuilderException):
    pass


class RebuilderExceptionGet(RebuilderException):
    pass


class RebuilderExceptionUpload(RebuilderException):
    pass


class RebuilderExceptionBuild(RebuilderException):
    pass


class RebuilderExceptionAttest(RebuilderException):
    pass


class RebuilderExceptionReport(RebuilderException):
    pass
