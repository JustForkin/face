
import sys
from collections import OrderedDict

from boltons.setutils import IndexedSet

from parser import Parser, Flag


def _get_default_name(frame_level=1):
    # TODO: is this a good idea? What if multiple parsers are created
    # in the same function for the sake of subparsers. This should
    # probably only be used from a classmethod or maybe a util
    # function.  TODO: what happens if a python module file contains a
    # non-ascii character?
    frame = sys._getframe(frame_level + 1)
    mod_name = frame.f_globals.get('__name__')
    if mod_name is None:
        return 'COMMAND'
    module = sys.modules[mod_name]
    if mod_name == '__main__':
        return module.__file__
    # TODO: reverse lookup entrypoint?
    return mod_name


class Command(object):
    def __init__(self, func, name, desc):
        name = name if name is not None else _get_default_name()
        self._parser = Parser(name, desc)
        # TODO: properties for name/desc/other parser things

        self.path_func_map = OrderedDict()
        self.path_func_map[()] = func

    @property
    def name(self):
        return self._parser.name

    @property
    def func(self):
        return self.path_func_map[()]

    @property
    def parser(self):
        return self._parser

    def add(self, *a, **kw):
        subcmd = a[0]
        if not isinstance(subcmd, Command) and callable(subcmd):
            subcmd = Command(*a, **kw)  # attempt to construct a new subcmd
        if isinstance(subcmd, Command):
            self._parser.add(subcmd.parser)
            # map in new functions
            for path in self._parser.subcmd_map:
                if path not in self.path_func_map:
                    self.path_func_map[path] = subcmd.path_func_map[path[1:]]
            return

        flag = a[0]
        if not isinstance(flag, Flag):
            flag = Flag(*a, **kw)  # attempt to construct a Flag from arguments
        self._parser.add(flag)

        return

    def run(self, argv=None):
        prs_res = self._parser.parse(argv=argv)
        func = self.path_func_map[prs_res.cmd]
        return func(prs_res)


"""Middleware thoughts:

* Clastic-like, but single function
* Mark with a @middleware(provides=()) decorator for provides

* Keywords (ParseResult members) end with _ (e.g., flags_), leaving
  injection namespace wide open for flags. With clastic, argument
  names are primarily internal, like a path parameter's name is not
  exposed to the user. With face, the flag names are part of the
  exposed API, and we don't want to reserve keywords or have
  excessively long prefixes.

* add() supports @middleware decorated middleware

* add_middleware() exists for non-decorated middleware functions, and
  just conveniently calls middleware decorator for you (decorator only
  necessary for provides)

Also Kurt says an easy way to access the subcommands to tweak them
would be useful. I think it's better to build up from the leaves than
to allow mutability that could trigger rechecks and failures across
the whole subcommand tree. Better instead to make copies of
subparsers/subcommands/flags and treat them as internal state.

"""
