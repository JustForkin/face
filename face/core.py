
import re
import sys
import keyword

from collections import OrderedDict

from boltons.iterutils import split
from boltons.dictutils import OrderedMultiDict as OMD


class Command(object):
    def __init__(self, name, desc, func):
        name = name if name is not None else _get_default_name()
        self._parser = Parser(name, desc)
        # TODO: properties for name/desc/other parser things
        self.func = func


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


# keep it just to subset of valid python identifiers for now
# TODO: switch [A-z] to [^\W\d_] for unicode support in future?
_VALID_FLAG_RE = re.compile(r"^[A-z][-_A-z0-9]*\Z")


def flag_to_attr_name(flag):
    # validate and canonicalize flag name. Basically, subset of valid
    # Python variable identifiers.
    #
    # Only letters, numbers, '-', and/or '_'. Only single/double
    # leading dash allowed (-/--). No trailing dashes or
    # underscores. Must not be a Python keyword.
    if not flag or not isinstance(flag, str):
        raise ValueError('expected non-zero length string for flag, not: %r' % flag)
    # TODO: possible exception, bare '--' used to separate args, but
    # this should be a builtin
    if flag.endswith('-') or flag.endswith('_'):
        raise ValueError('expected flag without trailing dashes'
                         ' or underscores, not: %r' % flag)

    lstripped = flag.lstrip('-')
    flag_match = _VALID_FLAG_RE.match(lstripped)
    if not flag_match:
        raise ValueError('valid flags must begin with one or two dashes, '
                         ' followed by a letter, and consist only of'
                         ' letters, digits, underscores, and dashes, not: %r'
                         % flag)
    len_diff = len(flag) - len(lstripped)
    if len_diff == 0 or len_diff > 2:
        raise ValueError('expected flag to start with "-" or "--", not: %r'
                         % flag)
    if len_diff == 1 and len(lstripped) > 1:
        raise ValueError('expected single-dash flag to consist of a single'
                         ' character, not: %r' % flag)

    flag_name = _normalize_flag_name(flag)

    if keyword.iskeyword(flag_name):
        raise ValueError('valid flags must not be a Python keyword: %r'
                         % flag)

    return flag_name


def process_subcmd_name(name):
    # validate and canonicalize flag name. Basically, subset of valid
    # Python variable identifiers.
    #
    # Only letters, numbers, '-', and/or '_'. Only single/double
    # leading dash allowed (-/--). No trailing dashes or
    # underscores. Must not be a Python keyword.
    if not name or not isinstance(name, str):
        raise ValueError('expected non-zero length string for subcommand name, not: %r' % name)
    # TODO: possible exception, bare '--' used to separate args, but
    # this should be a builtin
    if name.endswith('-') or name.endswith('_'):
        raise ValueError('expected subcommand name without trailing dashes'
                         ' or underscores, not: %r' % name)

    name_match = _VALID_FLAG_RE.match(name)
    if not name_match:
        raise ValueError('valid subcommand name must begin with a letter, and'
                         'consist only of letters, digits, underscores, and'
                         ' dashes, not: %r' % name)

    subcmd_name = _normalize_flag_name(name)

    return subcmd_name


def _normalize_flag_name(flag):
    ret = flag.lstrip('-')
    if (len(flag) - len(ret)) > 1:
        # only single-character flags are considered case-sensitive (like an initial)
        ret = ret.lower()
    ret = ret.replace('-', '_')
    return ret


def _arg_to_subcmd(arg):
    return arg.lower().replace('-', '_')


# TODO: CLISpec may be a better name for the top-level object
class Parser(object):
    """
    Parser parses, Command parses with Parser, then dispatches.
    """
    def __init__(self, name=None, desc=None, pos_args=None):
        if name is None:
            name = _get_default_name()
        if not name or name[0] in ('-', '_'):
            # TODO: more complete validation
            raise ValueError('expected name beginning with ASCII letter, not: %r' % (name,))
        self.name = name
        self.desc = desc
        # for now, assume this will accept a string as well as None/bool,
        # for use as the display name
        self.pos_args = pos_args
        # other conveniences that pos_args could maybe support to
        # reduce repetitive validation and error raising: min/max length
        self.flagfile_flag = Flag('--flagfile', parse_as=str, on_duplicate='add', required=False)

        self.subcmd_map = OrderedDict()
        self.path_flag_map = OrderedDict()
        self.path_flag_map[()] = OrderedDict()
        # TODO: should flagfile and help flags be hidden by default?

        if self.flagfile_flag:
            self.add(self.flagfile_flag)
        return

    def _add_subparser(self, subprs):
        # TODO: need to sort all conflict checking to the top
        if self.pos_args:
            raise ValueError('commands accepting positional arguments'
                             ' cannot take subcommands')
        # Copy in subcommands, checking for conflicts the name
        # comes from the parser, so if you want to add it as a
        # different name, make a copy of the parser.

        subprs_name = process_subcmd_name(subprs.name)
        for prs_path in self.subcmd_map:
            if prs_path[0] == subprs_name:
                raise ValueError('conflicting subcommand name: %r' % subprs_name)

        for subprs_path in subprs.subcmd_map:
            new_path = (subprs_name,) + subprs_path
            self.subcmd_map[new_path] = subprs

        self.subcmd_map[(subprs_name,)] = subprs

        # Flags inherit down (a parent's flags are usable by the child)
        # TODO: assume normalized, but need to check for conflicts
        parent_flag_map = self.path_flag_map[()]
        check_no_conflicts = lambda parent_flag_map, subcmd_path, subcmd_flags: True
        for path, flags in subprs.path_flag_map.items():
            if not check_no_conflicts(parent_flag_map, path, flags):
                # TODO
                raise ValueError('subcommand flags conflict with parent command: %r' % flags)
        for path, flags in subprs.path_flag_map.items():
            new_flags = parent_flag_map.copy()
            new_flags.update(flags)
            self.path_flag_map[(subprs_name,) + path] = new_flags

        # If two flags have the same name, as long as the "parse_as"
        # is the same, things should be ok. Need to watch for
        # overlapping aliases, too. This may allow subcommands to
        # further document help strings. Should the same be allowed
        # for defaults?

    def add(self, *a, **kw):
        if isinstance(a[0], Parser):
            subprs = a[0]
            self._add_subparser(subprs)
            return

        if isinstance(a[0], Flag):
            flag = a[0]
        else:
            try:
                flag = Flag(*a, **kw)
            except TypeError as te:
                raise ValueError('expected Parser, Flag, or Flag parameters,'
                                 ' not: %r, %r (got %r)' % (a, kw, te))

        # first check there are no conflicts...
        flag_name = flag_to_attr_name(flag.name)
        flag_map = self.path_flag_map[()]
        if flag_name in flag_map:
            # TODO: need a better error message here, one that
            # properly exposes the existing flag (same goes for
            # aliases below)
            raise ValueError('duplicate definition for flag name: %r' % flag_name)
        for alias in flag.alias_list:
            if flag_to_attr_name(alias) in flag_map:
                raise ValueError('conflicting alias for flag %r: %r' % (flag_name, alias))

        # ... then we add the flags
        flag_map[flag_name] = flag
        for alias in flag.alias_list:
            flag_map[flag_to_attr_name(alias)] = flag

        return

    def parse(self, argv):
        if argv is None:
            argv = sys.argv
        if not argv:
            raise ValueError('expected non-empty sequence of arguments, not: %r' % (argv,))
        # first snip off the first argument, the command itself
        cmd_name, argv = argv[0], list(argv)[1:]

        # next, split out the trailing args, if there are any
        trailing_args = None  # TODO: default to empty list?
        if '--' in argv:
            argv, trailing_args = split(argv, '--', 1)

        cmd_path = []
        flag_map = OMD()
        pos_args = []
        _consumed_val = False
        i = 0

        # first figure out the subcommand path
        for i, arg in enumerate(argv):
            if not arg:
                continue # TODO: how bad of an idea is it to ignore this?
            if arg[0] == '-':
                break  # subcmd parsing complete

            arg = _arg_to_subcmd(arg)
            cmd_path.append(arg)
            if tuple(cmd_path) not in self.subcmd_map:
                # TODO "unknown subcommand 'subcmd', choose from 'a',
                # 'b', 'c'." (also, did you mean...)
                raise ValueError('unknown subcommand: %r' % arg)

            break

        cmd_flag_map = self.path_flag_map[tuple(cmd_path)]

        for i, arg in enumerate(argv, i):
            if _consumed_val:
                _consumed_val = False
                continue
            if not arg:
                # TODO
                raise ValueError('unexpected empty arg between [...] and [...]')

            elif arg[0] == '-':
                # check presence in flag map, strip dashes
                # if arg == '-V':
                #    import pdb;pdb.set_trace()
                flag = cmd_flag_map.get(_normalize_flag_name(arg))
                if flag is None:
                    raise ValueError('unknown flag: %s' % arg)
                flag_key = _normalize_flag_name(flag.name)

                flag_conv = flag.parse_as
                if callable(flag_conv):
                    try:
                        arg_text = argv[i + 1]
                    except IndexError:
                        raise ValueError('expected argument for flag %r' % arg)
                    try:
                        arg_val = flag_conv(arg_text)
                    except Exception:
                        raise ValueError('flag %s converter (%r) failed to parse argument: %r'
                                         % (arg, flag_conv, arg_text))
                    flag_map.add(flag_key, arg_val)
                    _consumed_val = True
                else:
                    # e.g., True is effectively store_true, False is effectively store_false
                    flag_map.add(flag_key, flag_conv)
            elif False:  # TODO: flagfile
                pass
            else:
                if self.pos_args:
                    pos_args.extend(argv[i:])
                    break

        # resolve dupes and then...
        ret_flag_map = OrderedDict()
        for flag_name in flag_map:
            flag = cmd_flag_map[flag_name]
            arg_val_list = flag_map.getlist(flag_name)
            on_dup = flag.on_duplicate
            # TODO: move this logic into Flag.__init__
            if not on_dup or on_dup == 'error':
                if len(arg_val_list) > 1:
                    raise ValueError('more than one value passed for flag %s: %r'
                                     % (flag.name, arg_val_list))
                ret_flag_map[flag_name] = arg_val_list[0]
            elif on_dup == 'extend':
                ret_flag_map[flag_name] = arg_val_list
            elif on_dup == 'override':  # TODO: 'overwrite'?
                ret_flag_map[flag_name] = arg_val_list[-1]
            # TODO: 'ignore' aka pick first, as opposed to override's pick last

        # ... check requireds
        missing_flags = []
        for flag_name, flag in cmd_flag_map.items():
            if flag.required and flag_name not in ret_flag_map:
                missing_flags.append(flag.name)
        if missing_flags:
            raise ValueError('missing required arguments for flags: %s'
                             % ', '.join(missing_flags))

        args = CommandArguments(cmd_name, cmd_path, ret_flag_map, pos_args, trailing_args)
        return args

    def _parse_flag(self, flag):
        pass


# TODO: default
class Flag(object):
    def __init__(self, name, parse_as=True, required=False, alias=None, display_name=None, on_duplicate=None):
        # None = no arg
        # 'int' = int, 'float' = float, 'str' = str
        # List(int, sep=',', trim=True)  # see below
        # other = single callable
        self.name = name
        if not alias:
            alias = []
        elif isinstance(alias, str):
            alias = [alias]
        self.alias_list = list(alias)
        self.display_name = display_name or name
        self.parse_as = parse_as
        self.required = required
        # duplicates raise error by default
        # also have convenience param values: 'error'/'add'/'replace'
        # otherwise accept callable that takes argument + ArgResult
        self.on_duplicate = on_duplicate

    # TODO: __eq__ and copy



class ListParam(object):
    def __init__(self, arg_type=str, sep=',', trim=True):
        "basically a CSV as a parameter"
        # trim = trim each parameter in the list before calling arg_type


class FileValueParam(object):
    # Basically a file with a single value in it, like a pidfile
    # or a password file mounted in. Read in and treated like it
    # was on the argv.
    pass


class CommandArguments(object):
    def __init__(self, cmd_name, cmd_path, flag_map, pos_args, trailing_args):
        self.cmd_name = cmd_name
        self.cmd = tuple(cmd_path)
        self.flags = dict(flag_map)
        self.args = tuple(pos_args or ())
        self.trailing_args = trailing_args

    def __getattr__(self, name):
        """TODO: how to provide easy access to flag values while also
        providing access to "args" and "cmd" members. Could treat them
        as reserved keywords. Could "_"-prefix them. Could "_"-suffix
        them. Could "_"-suffix conflicting args so they'd still be
        accessible.

        Could return three things from parse(), but still have
        this issue when it comes to deciding what name to inject them
        as. probably need to make a reserved keyword.

        """
        return self.flags.get(_normalize_flag_name(name))


"""# Problems with argparse

argparse is a pretty solid library, and despite many competitors over
the years, the best argument parsing library available. Until now, of
course. Here's an inventory of problems argparse did not solve, and in
many ways, created.

* "Fuzzy" flag matching
* Inconvenient subcommand interface
* Flags at each level of the subcommand tree
* Positional arguments acceptable everywhere
* Bad help rendering (esp for subcommands)
* Inheritance-based API for extension with a lot of _*

At the end of the day, the real sin of argparse is that it enables the
creation of bad CLIs, often at the expense of ease of making good UIs
Despite this friction, argparse is far from infinitely powerful. As a
library, it is still relatively opinionated, and can only model a
somewhat-conventional UNIX-y CLI.

"""

x = 0

"""
clastic calls your function for you, should this do that, too?  is
there an advantage to sticking to the argparse route of handing back
a namespace? what would the signature of a CLI route be?
"""

x = 1

"""

* Specifying the CLI
* Wiring up the routing/dispatch
* OR Using the programmatic result of the parse (the Result object)
* Formatting the help messages?
* Using the actual CLI

"""

x = 2

"""

# "Types" discussion

* Should we support arbitrary validators (schema?) or go the clastic route and only basic types:
  * str
  * int
  * float
  * bool (TODO: default to true/false, think store_true, store_false in argparse)
  * list of the above
  * (leaning toward just basic types)


"""

x = 3

"""

- autosuggest on incorrect subcommand
- allow subcommand grouping
- hyphens and underscores equivalent for flags and subcommands

"""

x = 4

"""TODO: thought experiment

What about instead of requiring an explicit dependence up front,
provider functions had access to the current state and could raise a
special exception that simply said "wait". As long as we didn't
complete a full round of "waits", we'd be making progress.

There will of course be exceptions for stop/failure/errors.

Is this a bad design, or does it help with nuanced dependency
situations?

"""

x = 5

"""A command cannot have positional arguments _and_ subcommands.

Need to be able to set display name for pos_args

Which is the best default behavior for a flag? single flag where
presence=True (e.g., --verbose) or flag which accepts single string
arg (e.g., --path /a/b/c)

What to do if there appears to be flags after positional arguments?
How to differentiate between a bad flag and a positional argument that
starts with a dash?

"""

x = 6

""""Face: the CLI framework that's friendly to your end-user."

* Flag-first design that ensures flags stay consistent across all
  subcommands, for a more coherent API, less likely to surprise, more
  likely to delight.

(Note: need to do some research re: non-unicode flags to see how much
non-US CLI users care about em.)

Case-sensitive flags are bad for business *except for*
single-character flags (single-dash flags like -v vs -V).

TODO: normalizing subcommands

Should parse_as=List() with on_duplicate=extend give one long list or
a list of lists?

Parser is unable to determine which subcommands are valid leaf
commands, i.e., which ones can be handled as the last subcommand. The
Command dispatcher will have to raise an error if a specific
intermediary subcommand doesn't have a handler to dispatch to.

"""

x = 7

"""strata-minded thoughts:

* will need to disable and handle flagfiles separately if provenance
is going to be retained?

"""
