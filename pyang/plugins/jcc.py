#!/usr/bin/python
# -*- coding: latin-1 -*-
"""JCC: Java Contrail Client plug-in

   Copyright 2012 Tail-f Systems AB

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.

For complete functionality, invoke with:
> pyang \
    --path <yang search path> \
    --format jcc \
    --jcc-output <package.name> \
    --jcc-verbose \
    --jcc-ignore-errors \
    --jcc-import-on-demand \
    <file.yang>

Or, if you like to keep things simple:
> pyang -f jcc -d <package.name> <file.yang>

"""

import optparse
import os
import errno
import sys
import collections
import re

from datetime import date
from pyang import plugin, util, error

OSSep = "/"

def pyang_plugin_init():
    """Registers an instance of the jnc plugin"""
    plugin.register_plugin(JCCPlugin())


class JCCPlugin(plugin.PyangPlugin):
    """The plug-in class of JCC.

    The methods of this class are invoked by pyang during initialization. The
    emit method is of particular interest if you are new to writing plugins to
    pyang. It is from there that the parsing of the YANG statement tree
    emanates, producing the generated classes that constitutes the output of
    this plug-in.

    """

    def __init__(self):
        self.done = set([])  # Helps avoiding processing modules more than once

    def add_output_format(self, fmts):
        """Adds 'jcc' as a valid output format and sets the format to jcc if
        the -d/--jcc-output option is set, but -f/--format is not.

        """
        self.multiple_modules = False
        fmts['jcc'] = self

        args = sys.argv[1:]
        if not any(x in args for x in ('-f', '--format')):
            if any(x in args for x in ('-d', '--jcc-output')):
                sys.argv.insert(1, '--format')
                sys.argv.insert(2, 'jcc')

    def add_opts(self, optparser):
        """Adds options to pyang, displayed in the pyang CLI help message"""
        optlist = [
            optparse.make_option(
                '--jcc-output',
                dest='directory',
                help='Generate output to DIRECTORY.'),
            optparse.make_option(
                '--jcc-help',
                dest='jcc_help',
                action='store_true',
                help='Print help on usage of the JNC plugin and exit'),
            optparse.make_option(
                '--jcc-serial',
                dest='serial',
                action='store_true',
                help='Turn off usage of multiple threads.'),
            optparse.make_option(
                '--jcc-verbose',
                dest='verbose',
                action='store_true',
                help='Verbose mode: Print detailed debug messages.'),
            optparse.make_option(
                '--jcc-debug',
                dest='debug',
                action='store_true',
                help='Print debug messages. Redundant if verbose mode is on.'),
            optparse.make_option(
                '--jcc-ignore-errors',
                dest='ignore',
                action='store_true',
                help='Ignore errors from validation.'),
            optparse.make_option(
                '--jcc-import-on-demand',
                dest='import_on_demand',
                action='store_true',
                help='Use non explicit imports where possible.'),
            optparse.make_option(
                '--jcc-classpath-schema-loading',
                dest='classpath_schema_loading',
                action='store_true',
                help='Load schema files using classpath rather than location.')
            ]
        g = optparser.add_option_group('JCC output specific options')
        g.add_options(optlist)

    def setup_ctx(self, ctx):
        """Called after ctx has been set up in main module. Checks if the
        jnc help option was supplied and if not, that the -d or
        --java-package was used.

        ctx -- Context object as defined in __init__.py

        """
        if ctx.opts.jcc_help:
            self.print_help()
            sys.exit(0)
        if ctx.opts.format == 'jcc':
            if not ctx.opts.directory:
                ctx.opts.directory = 'src/gen'
                print_warning(msg=('Option -d (or --java-package) not set, ' +
                    'defaulting to "src/gen".'))
            #elif 'src' not in ctx.opts.directory:
            #    ctx.opts.directory = 'src/gen'
            #    print_warning(msg=('No "src" in output directory path, ' +
            #        'defaulting to "src/gen".'))

            # Fix path issue, the path in --jnc-output must contain src_managed/main
            if 'src_managed/main' in ctx.opts.directory:
                ctx.rootpkg = ctx.opts.directory.partition('src_managed/main')[2][1:]
                self.ctx = ctx
                self.d = ctx.opts.directory
            elif 'src' in ctx.opts.directory:
                ctx.rootpkg = ctx.opts.directory.rpartition('src')[2][1:]
                self.ctx = ctx
                self.d = ctx.opts.directory

    def setup_fmt(self, ctx):
        """Disables implicit errors for the Context"""
        ctx.implicit_errors = False

    def emit(self, ctx, modules, fd):
        """Generates XSD file from the YANG module supplied to pyang.

        The generated classes are placed in the directory specified by the '-d',
        or in "gen" if no such flag was provided,using the 'directory' attribute
        of ctx. If there are existing files in the output directory with the same
        name as the generated classes, they are silently overwritten.

        ctx     -- Context used to get output directory, verbosity mode, error
                   handling policy (the ignore attribute) and whether or not a
                   schema file should be generated.
        modules -- A list containing a module statement parsed from the YANG
                   module supplied to pyang.
        fd      -- File descriptor (ignored).

        """
        if ctx.opts.debug or ctx.opts.verbose:
            print('JCC plugin starting')
        if not ctx.opts.ignore:
            for (epos, etag, _) in ctx.errors:
                if (error.is_error(error.err_level(etag)) and
                    etag in ('MODULE_NOT_FOUND', 'MODULE_NOT_FOUND_REV')):
                    self.fatal("%s contains errors" % epos.top.arg)
                if (etag in ('TYPE_NOT_FOUND', 'FEATURE_NOT_FOUND',
                    'IDENTITY_NOT_FOUND', 'GROUPING_NOT_FOUND')):
                    print_warning(msg=(etag.lower() + ', generated class ' +
                        'hierarchy might be incomplete.'), key=etag)
                else:
                    print_warning(msg=(etag.lower() + ', aborting.'), key=etag)
                    self.fatal("%s contains errors" % epos.top.arg)


        # Sweep, adding included and imported modules, until no change
        module_set = set(modules)
        # num_modules = 0
        # while num_modules != len(module_set):
        #     num_modules = len(module_set)
        #     for module in list(module_set):
        #         imported = map(lambda x: x.arg, search(module, 'import'))
        #         included = map(lambda x: x.arg, search(module, 'include'))
        #         for (module_stmt, rev) in self.ctx.modules:
        #             if module_stmt in (imported + included):
        #                 module_set.add(self.ctx.modules[(module_stmt, rev)])

        # Generate files from main modules
        for module in filter(lambda s: s.keyword == 'module', module_set):
            self.generate_from(module)

        # Generate files from augmented modules
        # for aug_module in augmented_modules.values():
        #     self.generate_from(aug_module)

        # Print debug messages saying that we're done.
        if ctx.opts.debug or ctx.opts.verbose:
            print('XSD generation COMPLETE.')
            #if not self.ctx.opts.no_schema:
            #    print('Schema generation COMPLETE.')

    def generate_from(self, module):
        """Generates classes, schema file and pkginfo files for module,
        according to options set in self.ctx. The attributes self.directory
        and self.d are used to determine where to generate the files.

        module -- Module statement to generate files from

        """
        if module in self.done:
            return
        self.done.add(module)
        subpkg = camelize(module.arg)

        d = OSSep.join([self.d , subpkg])

        # Generate XSD file content
        src = ('module "' + module.arg + '", revision: "' +
            util.get_latest_revision(module) + '".')
        generator = ClassGenerator(module,
            path=OSSep.join([self.ctx.opts.directory]), src=src, ctx=self.ctx)
        generator.generate()

    def fatal(self, exitCode=1):
        """Raise an EmitError"""
        raise error.EmitError(self, exitCode)

    def print_help(self):
        """Prints a description of what JNC is and how to use it"""
        print('''
The JNC (Java NETCONF Client) plug-in can be used to generate a Java class
hierarchy from a single YANG data model. Together with the JNC library, these
generated Java classes may be used as the foundation for a NETCONF client
(AKA manager) written in Java.

The different types of generated files are:

Root class  -- This class has the name of the prefix of the YANG module, and
               contains fields with the prefix and namespace as well as methods
               that enables the JNC library to use the other generated classes
               when interacting with a NETCONF server.

YangElement -- Each YangElement corresponds to a container, list or
               notification in the YANG model. They represent tree nodes of a
               configuration and provides methods to modify the configuration
               in accordance with the YANG model that they were generated from.

               The top-level nodes in the YANG model will have their
               corresponding YangElement classes generated in the output
               directory together with the root class. Their respective
               subnodes are generated in subpackages with names corresponding
               to the name of the parent.

YangTypes   -- For each derived type in the YANG model, a class is generated to
               the root of the output directory. The derived type may either
               extend another derived type class, or the JNC type class
               corresponding to a built-in YANG type.

Packageinfo -- For each package in the generated Java class hierarchy, a
               package-info.java file is generated, which can be useful when
               generating javadoc for the hierarchy.

Schema file -- If enabled, an XML file containing structured information about
               the generated Java classes is generated. It contains tagpaths,
               namespace, primitive-type and other useful meta-information.

The typical use case for these classes is as part of a JAVA network management
system (EMS), to enable retrieval and/or storing of configurations on NETCONF
agents/servers with specific capabilities.

One way to use the JNC plug-in of pyang is
$ pyang -f jnc --jnc-output package.dir <file.yang>

Type '$ pyang --help' for more details on how to use pyang.
''')


com_tailf_jnc = {'Attribute', 'Capabilities', 'ConfDSession',
                 'DefaultIOSubscriber', 'Device', 'DeviceUser', 'DummyElement',
                 'Element', 'ElementChildrenIterator', 'ElementHandler',
                 'ElementLeafListValueIterator', 'IOSubscriber',
                 'JNCException', 'Leaf', 'NetconfSession', 'NodeSet', 'Path',
                 'PathCreate', 'Prefix', 'PrefixMap', 'RevisionInfo',
                 'RpcError', 'SchemaNode', 'SchemaParser', 'SchemaTree',
                 'SSHConnection', 'SSHSession', 'Tagpath', 'TCPConnection',
                 'TCPSession', 'Transport', 'Utils', 'XMLParser',
                 'YangBaseInt', 'YangBaseString', 'YangBaseType', 'YangBinary',
                 'YangBits', 'YangBoolean', 'YangDecimal64', 'YangElement',
                 'YangEmpty', 'YangEnumeration', 'YangException',
                 'YangIdentityref', 'YangInt16', 'YangInt32', 'YangInt64',
                 'YangInt8', 'YangLeafref', 'YangString', 'YangType',
                 'YangUInt16', 'YangUInt32', 'YangUInt64', 'YangUInt8',
                 'YangUnion', 'YangXMLParser', 'YangJsonParser'}


java_reserved_words = {'abstract', 'assert', 'boolean', 'break', 'byte',
                       'case', 'catch', 'char', 'class', 'const', 'continue',
                       'default', 'double', 'do', 'else', 'enum', 'extends',
                       'false','final', 'finally', 'float', 'for', 'goto',
                       'if', 'implements', 'import', 'instanceof', 'int',
                       'interface', 'long', 'native', 'new', 'null', 'package',
                       'private', 'protected', 'public', 'return', 'short',
                       'static', 'strictfp', 'super', 'switch', 'synchronized',
                       'this', 'throw', 'throws', 'transient', 'true', 'try',
                       'void', 'volatile', 'while'}
"""A set of all identifiers that are reserved in Java"""


java_literals = {'true', 'false', 'null'}
"""The boolean and null literals of Java"""


java_lang = {'Appendable', 'CharSequence', 'Cloneable', 'Comparable',
             'Iterable', 'Readable', 'Runnable', 'Boolean', 'Byte',
             'Character', 'Class', 'ClassLoader', 'Compiler', 'Double',
             'Enum', 'Float', 'Integer', 'Long', 'Math', 'Number',
             'Object', 'Package', 'Process', 'ProcessBuilder',
             'Runtime', 'RuntimePermission', 'SecurityManager',
             'Short', 'StackTraceElement', 'StrictMath', 'String',
             'StringBuffer', 'StringBuilder', 'System', 'Thread',
             'ThreadGroup', 'ThreadLocal', 'Throwable', 'Void'}
"""A subset of the java.lang classes"""


java_util = {'Collection', 'Enumeration', 'Iterator', 'List', 'ListIterator',
             'Map', 'Queue', 'Set', 'ArrayList', 'Arrays', 'HashMap',
             'HashSet', 'Hashtable', 'LinkedList', 'Properties', 'Random',
             'Scanner', 'Stack', 'StringTokenizer', 'Timer', 'TreeMap',
             'TreeSet', 'UUID', 'Vector'}
"""A subset of the java.util interfaces and classes"""


java_built_in = java_reserved_words | java_literals | java_lang
"""Identifiers that shouldn't be imported in Java"""


yangelement_stmts = {'container', 'list', 'leaf', 'leaf-list'}
"""Keywords of statements that YangElement classes are generated from"""


leaf_stmts = {'leaf', 'leaf-list'}
"""Leaf and leaf-list statement keywords"""


module_stmts = {'module', 'submodule'}
"""Module and submodule statement keywords"""


node_stmts = module_stmts | yangelement_stmts | leaf_stmts
"""Keywords of statements that make up a configuration tree"""


package_info = '''/**
 * This class hierarchy was generated from the Yang module{0}
 * by the <a target="_top" href="https://github.com/tail-f-systems/JNC">JNC</a> plugin of <a target="_top" href="http://code.google.com/p/pyang/">pyang</a>.
 * The generated classes may be used to manipulate pieces of configuration data
 * with NETCONF operations such as edit-config, delete-config and lock. These
 * operations are typically accessed through the JNC Java library by
 * instantiating Device objects and setting up NETCONF sessions with real
 * devices using a compatible YANG model.
 * <p>{1}
 * @see <a target="_top" href="https://github.com/tail-f-systems/JNC">JNC project page</a>
 * @see <a target="_top" href="ftp://ftp.rfc-editor.org/in-notes/rfc6020.txt">RFC 6020: YANG - A Data Modeling Language for the Network Configuration Protocol (NETCONF)</a>
 * @see <a target="_top" href="ftp://ftp.rfc-editor.org/in-notes/rfc6241.txt">RFC 6241: Network Configuration Protocol (NETCONF)</a>
 * @see <a target="_top" href="ftp://ftp.rfc-editor.org/in-notes/rfc6242.txt">RFC 6242: Using the NETCONF Protocol over Secure Shell (SSH)</a>
 * @see <a target="_top" href="http://www.tail-f.com">Tail-f Systems</a>
 */
 package '''
"""Format string used in package-info files"""


outputted_warnings = []
"""A list of warning message IDs that are used to avoid duplicate warnings"""


augmented_modules = {}
"""A dict of external modules that are augmented by the YANG module"""


camelized_stmt_args = {}
"""Cache containing camelized versions of statement identifiers"""


normalized_stmt_args = {}
"""Cache containing normalized versions of statement identifiers"""


class_hierarchy = {}
"""Dict that map package names to sets of names of classes to be generated"""

yang_to_xsd_types = {
    "string": "xsd:string",
    "int8": "xsd:byte",
    "int16": "xsd:short",
    "int32": "xsd:int",
    "int64": "xsd:long",
    "uint8": "xsd:unsignedByte",
    "uint16": "xsd:unsignedShort",
    "uint32": "xsd:unsignedInt",
    "uint64": "xsd:unsignedLong",
    "decimal64": "xsd:decimal",
    "boolean": "xsd:boolean"
}


def print_warning(msg='', key='', ctx=None):
    """Prints msg to stderr if ctx is None or the debug or verbose flags are
    set in context ctx and key is empty or not in outputted_warnings. If key is
    not empty and not in outputted_warnings, it is added to it. If msg is empty
    'No support for type "' + key + '", defaulting to string.' is printed.

    """
    if ((not key or key not in outputted_warnings) and
        (not ctx or ctx.opts.debug or ctx.opts.verbose)):
        if msg:
            sys.stderr.write('WARNING: ' + msg)
            if key:
                outputted_warnings.append(key)
        else:
            print_warning(('No support for type "' + key + '", defaulting ' +
                'to string.'), key, ctx)


def write_file(d, file_name, file_content, ctx):
    """Creates the directory d if it does not yet exist and writes a file to it
    named file_name with file_content in it.

    """
    #d = d.replace('.', OSSep)
    wd = os.getcwd()
    try:
        os.makedirs(d, 0o777)
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            pass  # The directory already exists
        else:
            raise
    try:
        os.chdir(d)
    except OSError as exc:
        if exc.errno == errno.ENOTDIR:
            print_warning(msg=('Unable to change directory to ' + d +
                '. Probably a non-directory file with same name as one of ' +
                'the subdirectories already exists.'), key=d, ctx=ctx)
        else:
            raise
    finally:
        if ctx.opts.verbose:
            print('Writing file to: ' + os.getcwd() + OSSep + file_name)
        os.chdir(wd)
    with open(d + OSSep + file_name, 'w+') as f:
        if isinstance(file_content, str):
            f.write(file_content)
        else:
            for line in file_content:
                f.write(line)
                f.write('\n')


def get_module(stmt):
    """Returns the module to which stmt belongs to"""
    if stmt.top is not None:
        return get_module(stmt.top)
    elif stmt.keyword == 'module':
        return stmt
    else:  # stmt.keyword == 'submodule':
        belongs_to = search_one(stmt, 'belongs-to')
        for (module_name, revision) in stmt.i_ctx.modules:
            if module_name == belongs_to.arg:
                return stmt.i_ctx.modules[(module_name, revision)]


def get_parent(stmt):
    """Returns closest parent which is not a choice, case or submodule
    statement. If the parent is a submodule statement, the corresponding main
    module is returned instead.

    """
    if stmt.parent is None:
        return None
    elif stmt.parent.keyword == 'submodule':
        return get_module(stmt)
    elif stmt.parent.parent is None:
        return stmt.parent
    elif stmt.parent.keyword in ('choice', 'case'):
        return get_parent(stmt.parent)
    else:
        return stmt.parent


def get_package(stmt, ctx):
    """Returns a string representing the package name of a java class generated
    from stmt, assuming that it has been or will be generated by JNC.

    """
    sub_packages = collections.deque()
    parent = get_parent(stmt)
    while parent is not None:
        if stmt.i_orig_module.keyword == "submodule" and stmt.keyword != "typedef" and get_parent(parent) is None:
            sub_packages.appendleft(camelize(stmt.i_orig_module.arg))
        stmt = parent
        parent = get_parent(stmt)
        sub_packages.appendleft(camelize(stmt.arg))

    full_package = ctx.rootpkg.split(OSSep)
    full_package.extend(['mo'])
    full_package.extend(sub_packages)
    return '.'.join(full_package)

def get_parents(stmt):
    sub_packages = collections.deque()
    parent = get_parent(stmt)
    while parent is not None and parent.keyword != "submodule" and parent.keyword != "module":
        stmt = parent
        parent = get_parent(stmt)
        sub_packages.appendleft(stmt)

    return sub_packages

def pairwise(iterable):
    """Returns an iterator that includes the next item also"""
    iterator = iter(iterable)
    item = next(iterator)  # throws StopIteration if empty.
    for next_item in iterator:
        yield (item, next_item)
        item = next_item
    yield (item, None)


def capitalize_first(string):
    """Returns string with its first character capitalized (if any)"""
    return string[:1].capitalize() + string[1:]


def decapitalize_first(string):
    """Returns string with its first character decapitalized (if any)"""
    return string[:1].lower() + string[1:]


def camelize(string):
    """Converts string to lower camel case

    Removes hyphens and dots and replaces following character (if any) with
    its upper-case counterpart. Does not remove consecutive or trailing hyphens
    or dots.

    If the resulting string is reserved in Java, an underline is appended

    Returns an empty string if string argument is None. Otherwise, returns
    string decapitalized and with no consecutive upper case letters.

    """
    try:  # Fetch from cache
        return camelized_stmt_args[string]
    except KeyError:
        pass
    camelized_str = collections.deque()
    if string is not None:
        iterator = pairwise(decapitalize_first(string))
        for character, next_character in iterator:
            if next_character is None:
                if (len(string) > 1):
                    camelized_str.append(character)
                else:
                    if(string.isupper()):
                        camelized_str.append(character.upper())
                    else:
                        camelized_str.append(character.lower())
            elif character in '-._':
                camelized_str.append(capitalize_first(next_character))
                next(iterator)
            elif (character.isupper()
                  and (next_character.isupper()
                       or not next_character.isalpha())):
                camelized_str.append(character.lower())
            else:
                camelized_str.append(character)
    res = ''.join(camelized_str)
    if res in java_reserved_words | java_literals:
        camelized_str.append('_')
    if re.match(r'\d', res):
        camelized_str.appendleft('_')
    res = ''.join(camelized_str)
    camelized_stmt_args[string] = res  # Add to cache
    return res


def normalize(string):
    """returns capitalize_first(camelize(string)), except if camelize(string)
    begins with and/or ends with a single underline: then they are/it is
    removed and a 'J' is prepended. Mimics normalize in YangElement of JNC.

    """
    try:  # Fetch from cache
        return normalized_stmt_args[string]
    except KeyError:
        pass
    res = camelize(string)
    start = 1 if res.startswith('_') else 0
    end = -1 if res.endswith('_') else 0
    if start or end:
        res = capitalize_first(res[start:end])
    else:
        res = capitalize_first(res)
    normalized_stmt_args[string] = res  # Add to cache
    return res


def flatten(l):
    """Returns a flattened version of iterable l

    l must not have an attribute named values unless the return value values()
    is a valid substitution of l. Same applies to all items in l.

    Example: flatten([['12', '34'], ['56', ['7']]]) = ['12', '34', '56', '7']
    """
    res = []
    while hasattr(l, 'values'):
        l = list(l.values())
    for item in l:
        try:
            assert not isinstance(item, str)
            iter(item)
        except (AssertionError, TypeError):
            res.append(item)
        else:
            res.extend(flatten(item))
    return res


def get_types(yang_type, ctx):
    """Returns jnc and primitive counterparts of yang_type, which is a type,
    typedef, leaf or leaf-list statement.

    """
    if yang_type.keyword in leaf_stmts:
        yang_type = search_one(yang_type, 'type')
    assert yang_type.keyword in ('type', 'typedef'), 'argument is type, typedef or leaf'
    if yang_type.arg == 'leafref':
        return get_types(yang_type.parent.i_leafref.i_target_node, ctx)
    primitive = normalize(yang_type.arg)
    if yang_type.keyword == 'typedef':
        primitive = normalize(get_base_type(yang_type).arg)
    if primitive == 'JBoolean':
        primitive = 'Boolean'
    jnc = 'com.tailf.jnc.Yang' + primitive
    if yang_type.arg in ('string', 'boolean'):
        pass
    elif yang_type.arg in ('enumeration', 'binary', 'union', 'empty',
                           'instance-identifier', 'identityref'):
        primitive = 'String'
    elif yang_type.arg in ('bits',):  # uint64 handled below
        primitive = 'BigInteger'
    elif yang_type.arg == 'decimal64':
        primitive = 'BigDecimal'
    elif yang_type.arg in ('int8', 'int16', 'int32', 'int64', 'uint8',
            'uint16', 'uint32', 'uint64'):
        integer_type = ['long', 'int', 'short', 'byte']
        if yang_type.arg[:1] == 'u':  # Unsigned
            integer_type.pop()
            integer_type.insert(0, 'BigInteger')
            jnc = 'com.tailf.jnc.YangUI' + yang_type.arg[2:]
        if yang_type.arg[-2:] == '64':
            primitive = integer_type[0]
        elif yang_type.arg[-2:] == '32':
            primitive = integer_type[1]
        elif yang_type.arg[-2:] == '16':
            primitive = integer_type[2]
        else:  # 8 bits
            primitive = integer_type[3]
    else:
        try:
            typedef = yang_type.i_typedef
        except AttributeError:
            if yang_type.keyword == 'typedef':
                primitive = normalize(yang_type.arg)
            else:
                pkg = get_package(yang_type, ctx)
                name = normalize(yang_type.arg)
                print_warning(key=pkg  + '.' + name, ctx=ctx)
        else:
            basetype = get_base_type(typedef)
            jnc, primitive = get_types(basetype, ctx)
            if get_parent(typedef).keyword in ('module', 'submodule'):
                package = get_package(typedef, ctx)
                typedef_arg = normalize(typedef.arg)
                jnc = package + '.' + typedef_arg
    return jnc, primitive


def get_base_type(stmt):
    """Returns the built in type that stmt is derived from"""
    if stmt.keyword == 'type' and stmt.arg == 'union':
        return stmt
    type_stmt = search_one(stmt, 'type')
    if type_stmt is None:
        return stmt
    try:
        typedef = type_stmt.i_typedef
    except AttributeError:
        return type_stmt
    else:
        if typedef is not None:
            return get_base_type(typedef)
        else:
            return type_stmt


def get_import(string):
    """Returns a string representing a class that can be imported in Java.

    Does not handle Generics or Array types and is data model agnostic.

    """
    if string.startswith(('java.math', 'java.util', 'com.tailf.jnc')):
        return string
    elif string in ('BigInteger', 'BigDecimal'):
        return '.'.join(['java.math', string])
    elif string in java_util:
        return '.'.join(['java.util', string])
    else:
        return '.'.join(['com.tailf.jnc', string])


def search(stmt, keywords):
    """Utility for calling Statement.search. If stmt has an i_children
    attribute, they are searched for keywords as well.

    stmt     -- Statement to search for children in
    keywords -- A string, or a tuple, list or set of strings, to search for

    Returns a set (without duplicates) of children with matching keywords.
    If choice or case is not in keywords, substatements of choice and case
    are searched as well.

    """
    if isinstance(keywords, str):
        keywords = keywords.split()
    bypassed = ('choice', 'case')
    bypass = all(x not in keywords for x in bypassed)
    dict_ = collections.OrderedDict()

    def iterate(children, acc):
        for ch in children:
            if bypass and ch.keyword in bypassed:
                _search(ch, keywords, acc)
                continue
            try:
                key = ' '.join([ch.keyword, camelize(ch.arg)])
            except TypeError:
                if ch.arg is None:  # Extension
                    key = ' '.join(ch.keyword)
                else:
                    key = ' '.join([':'.join(ch.keyword), camelize(ch.arg)])
            if key in acc:
                continue
            for keyword in keywords:
                if ch.keyword == keyword:
                    acc[key] = ch
                    break

    def _search(stmt, keywords, acc):
        if any(x in keywords for x in ('typedef', 'import',
                                       'augment', 'include')):
            old_keywords = keywords[:]
            keywords = ['typedef', 'import', 'augment', 'include']
            iterate(stmt.substmts, acc)
            keywords = old_keywords
        try:
            iterate(stmt.i_children, acc)
            if not dict_:
                iterate(stmt.substmts, acc)
        except AttributeError:
            iterate(stmt.substmts, acc)

    _search(stmt, keywords, dict_)
    return list(dict_.values())


def search_one(stmt, keyword, arg=None):
    """Utility for calling Statement.search_one, including i_children."""
    res = stmt.search_one(keyword, arg=arg)
    if res is None:
        try:
            res = stmt.search_one(keyword, arg=arg, children=stmt.i_children)
        except AttributeError:
            pass
    if res is None:
        try:
            return search(stmt, keyword)[0]
        except IndexError:
            return None
    return res

def search_one_raw(self, raw_keyword, arg=None, children=None):
    """Return receiver's substmt with `keyword` and optionally `arg`.
    """
    if children is None:
        children = self.substmts
    for ch in children:
        if ch.raw_keyword == raw_keyword and (arg is None or ch.arg == arg):
            return ch
    return None

def is_config(stmt):
    """Returns True if stmt is a configuration data statement"""
    config = None
    while config is None and stmt is not None:
        if stmt.keyword == 'notification':
            return False # stmt is not config if part of a notification tree
        config = search_one(stmt, 'config')
        stmt = get_parent(stmt)
    return config is None or config.arg == 'true'

def is_include_yangelement(stmt):
    """Returns True if stmt include a yangelement_stmt data statement"""
    for ch in stmt.substmts:
        for keyword in list(yangelement_stmts | {'rpc'}):
            if ch.keyword == keyword:
                return True
    return False

def get_typename(stmt):
    t = search_one(stmt, 'type')
    if t is not None:
        return t.arg
    else:
       return ''


class YangType(object):
    """Provides an interface to maintain a list of defined yang types"""

    def __init__(self):
        self.defined_types = ['empty', 'int8', 'int16', 'int32', 'int64',
            'uint8', 'uint16', 'uint32', 'uint64', 'binary', 'bits', 'boolean',
            'decimal64', 'enumeration', 'identityref', 'instance-identifier',
            'leafref', 'string', 'union']  # Use set instead!
        """List of types represented by a jnc or generated class"""

    def defined(self, yang_type):
        """Returns true if yang_type is defined, else false"""
        return (yang_type in self.defined_types)

    def add(self, yang_type):
        """Gives yang_type "defined" status in this instance of YangType"""
        self.defined_types.append(yang_type)


class ClassGenerator(object):
    """Used to generate java classes from a yang module"""

    def __init__(self, stmt, path=None, package=None, mopackage=None, src=None, ctx=None,
                 ns='', prefix_name='', yang_types=None, parent=None):
        """Constructor.

        stmt        -- A statement (sub)tree, parsed from a YANG model
        path        -- Full path to where the class should be written
        package     -- Name of Java package
        src         -- Filename of parsed yang module, or the module name and
                       revision if filename is unknown
        ctx         -- Context used to fetch option parameters
        ns          -- The XML namespace of the module
        prefix_name -- The module prefix
        yang_types  -- An instance of the YangType class
        parent      -- ClassGenerator to copy arguments that were not supplied
                       from (if applicable)

        """
        self.stmt = stmt
        self.path = path
        self.package = None if package is None else package.replace(OSSep, '.')
        # self.mopackage = None if mopackage is None else mopackage.replace(OSSep, '.')
        self.src = src
        self.ctx = ctx
        self.ns = ns
        self.prefix_name = prefix_name
        self.yang_types = yang_types

        self.n = normalize(stmt.arg)
        self.n2 = camelize(stmt.arg)

        self.ucg = set([]) # track uses choice groups to avoid duplicate
        if yang_types is None:
            self.yang_types = YangType()
        # if parent is not None:
        #     for attr in ('package', 'src', 'ctx', 'path', 'ns',
        #                  'prefix_name', 'yang_types'):
        #         if getattr(self, attr) is None:
        #             setattr(self, attr, getattr(parent, attr))
        #
        #     module = get_module(stmt)
        #     if self.ctx.rootpkg:
        #         self.rootpkg = '.'.join([self.ctx.rootpkg.replace(OSSep, '.'),
        #                                  camelize(module.arg)])
        #     else:
        #         self.rootpkg = camelize(module.arg)
        # else:
        #     self.rootpkg = package

    def generate(self):
        """Generates class(es) for self.stmt"""
        if self.stmt.keyword in ('module', 'submodule'):
            self.generate_routeclass(self.stmt)
        # elif self.stmt.keyword in ('list', 'container'):
        #     if search_one(self.stmt, ('csp-common', 'vertex')) or search_one(self.stmt, ('csp-common', 'edge')) :
        #         self.generate_class()

    # def generate_classes(self):
    #     """Generates a Java class hierarchy from a module statement, allowing
    #     for netconf communication using the jnc library.
    #
    #     """
        # assert(self.stmt.keyword == 'module')
        #
        # is_yangelement = is_include_yangelement(self.stmt)
        #
        # if is_yangelement:
        #     module_stmts = set([self.stmt])
        # else:
        #     module_stmts = set([])

        #for (module, rev) in self.ctx.modules:
        #     if module in included:
        #         module_stmts.add(self.ctx.modules[(module, rev)])

        # for module in module_stmts:
        #     self.generate_routeclass(module)

    def generate_routeclass(self, module):
        """Generates a XSD file hierarchy from a module and include statement

        module        -- A statement (sub)tree represent module or submodule, parsed from a YANG model
        """
        filename = module.arg.replace("-", "_") + '.xsd'

        # if module.keyword == "module":
        #     path = self.path
        # elif module.keyword == "submodule":
        #     path = self.path + "/" + camelize(module.arg)

        # Generate routes class
        if self.ctx.opts.verbose:
            print('Generating xsd file "' + filename + '"...')

        self.java_class = JavaClass(filename=filename, source=module.arg)

        module_set = set([module])
        included = map(lambda x: x.arg, search(module, 'include'))
        for (module_stmt, rev) in self.ctx.modules:
            if module_stmt in included:
                module_set.add(self.ctx.modules[(module_stmt, rev)])

        for module in filter(lambda s: s.keyword == 'module'or s.keyword == 'submodule', module_set):
            res = search(module, list(yangelement_stmts | {'augment', 'rpc'}))
            group =search(module, "grouping")
            typedef =search(module, "typedef")
            if (len(group) > 0):
                for stmt in search(module, "grouping"):
                    self.generate_grouping(stmt)
            if (len(typedef) > 0):
                for stmt in search(module, "typedef"):
                    self.generate_typedef(stmt)
            if (len(res) > 0):
                # Generate classes for children of module/submodule
                for stmt in search(module, list(yangelement_stmts | {'rpc', 'notification'})):
                    # Do not generate include stmt in submodule
                    if stmt.i_orig_module.arg == module.arg:
                        self.generate_xsd_elements(stmt)

        write_file(self.path,
           filename,
           self.java_class.as_list(),
           self.ctx)

    def generate_grouping(self, stmt):
        file_indent = ' ' * 4
        indent = ' ' * 8
        body_indent = ' ' * 12
        exact = []

        if len(stmt.i_children) == 1 and stmt.i_children[0].keyword == "list":
            exact.append(file_indent+'<xsd:complexType name="'+normalize(stmt.arg)+'Type" >')
            for sub_stmt in search(stmt, list(yangelement_stmts)):
                exact.append(indent+'<xsd:all>')
                for property in search(sub_stmt, ["leaf", "container" ]):
                    type = search_one(property, 'type')
                    prefix = type.arg.split(':')[0]
                    if type.arg in yang_to_xsd_types:
                        type_name = yang_to_xsd_types[type.arg]
                    elif type.arg == "enumeration":
                        # generate enumeration type
                        self.generate_enumtype(property.arg, type)
                        type_name = normalize(property.arg)+'Type'
                    elif prefix in ["inet", "yang"]:
                        type_name = type.arg
                    else:
                        type_name = normalize(type.arg)+"Type"

                    uses = search_one(property, 'uses')
                    if uses:
                        type_name = uses.arg

                    if property.parent.arg == sub_stmt.arg:
                        exact.append(body_indent+'<xsd:element name="'+property.arg+'" type="'+type_name+'"/>')
                 
                for property in  search(sub_stmt, "leaf-list"):
                    type = search_one(property, 'type')
                    type_arg = self.get_build_in_type(property).arg
                    if type_arg in yang_to_xsd_types:
                        type_name = yang_to_xsd_types[type_arg]
                    elif type.arg == "enumeration":
                        # generate enumeration type
                        self.generate_enumtype(property.arg, type)
                        type_name = normalize(property.arg)+'Type'
                    elif type_arg in ("union", "type"):
                        type_name = type.arg
                    else:
                        type_name = normalize(type.arg)+"Type"

                    exact.append(body_indent+'<xsd:element name="'+property.arg+'" type="'+type_name+'" maxOccurs="unbounded" />')

                choice = search_one(sub_stmt, "choice")
                 
                if choice:
                    type_name = normalize(sub_stmt.arg+choice.arg)
                    self.generate_choicetype(type_name, choice)
                    exact.append(body_indent+'<xsd:element name="'+choice.arg+'" type="'+type_name+'Type" />')

                exact.append(indent+'</xsd:all>')
            exact.append(file_indent+'</xsd:complexType>')

        choice = search_one(stmt, "choice")
        if choice:
            type_name = normalize(stmt.arg+choice.arg)
            self.generate_choicetype(type_name, choice)
            #exact.append(file_indent+'<xsd:complexType name="'+normalize(stmt.arg)+'Type">')
            #exact.append(indent+'<xsd:all>')
            #exact.append(body_indent+'<xsd:element name="'+choice.arg+'" type="'+type_name+'"/>')
            #exact.append(indent+'</xsd:all>')
            #exact.append(file_indent+'</xsd:complexType>')

        grouping = JavaValue(exact)
        self.java_class.append_access_method("a_grouping", grouping)

    def generate_typedef(self, stmt):
        file_indent = ' ' * 4
        indent = ' ' * 8
        body_indent = ' ' * 12
        exact = []

        type = search_one(stmt, 'type')
        if type.arg == "enumeration":
            # generate enumeration type
            self.generate_enumtype(stmt.arg, type)

        if hasattr(stmt, 'i_children') and stmt.i_children[0].keyword == "list":
            exact.append(file_indent+'<xsd:complexType name="'+normalize(stmt.arg)+'Type" >')
            for sub_stmt in search(stmt, list(yangelement_stmts)):
                exact.append(indent+'<xsd:all>')
                for property in search(sub_stmt, ["leaf", "container"]):
                    type = search_one(property, 'type')
                    type_name = None
                    if type and type.arg == "string":
                        type_name = "xsd:string"
                    uses = search_one(property, 'uses')
                    if uses:
                        type_name = uses.arg
                    if not type_name:
                        type_name=type.arg
                    if property.parent.arg == sub_stmt.arg:
                        exact.append(body_indent+'<xsd:element name="'+property.arg+'" type="'+type_name+'"/>')

                choice = search_one(sub_stmt, "choice")

                if choice:
                    type_name = normalize(sub_stmt.arg+choice.arg)
                    self.generate_choicetype(type_name, choice)
                    exact.append(body_indent+'<xsd:element name="'+choice.arg+'" type="'+type_name+'Type" />')

                exact.append(indent+'</xsd:all>')
            exact.append(file_indent+'</xsd:complexType>')

        choice = search_one(stmt, "choice")
        if choice:
            type_name = normalize(stmt.arg+choice.arg)
            self.generate_choicetype(type_name, choice)
            #exact.append(file_indent+'<xsd:complexType name="'+normalize(stmt.arg)+'Type">')
            #exact.append(indent+'<xsd:all>')
            #exact.append(body_indent+'<xsd:element name="'+choice.arg+'" type="'+type_name+'"/>')
            #exact.append(indent+'</xsd:all>')
            #exact.append(file_indent+'</xsd:complexType>')

        typedef = JavaValue(exact)
        self.java_class.append_access_method("a_typedef", typedef)

    def generate_choicetype(self, choice_type_name, choice):
        choice_stmt = []
        file_indent = '\t'
        indent = '\t' * 2
        body_indent = '\t' * 3

        choice_stmt.append(file_indent+'<xsd:complexType name="'+choice_type_name+'Type" >')
        choice_stmt.append(indent+'<xsd:choice>')
        for value in search(choice, "case"):
            case_typename = normalize(choice_type_name+value.arg)
            self.generate_type(case_typename, value)
            choice_stmt.append(body_indent+'<xsd:element name="'+value.arg+'" type="'+case_typename+'Type" />')
        choice_stmt.append(indent+'</xsd:choice>')
        choice_stmt.append(file_indent+'</xsd:complexType>')

        choice_value = JavaValue(choice_stmt)
        self.java_class.add_xsd_type("choice_"+choice_type_name, choice_value)

    def generate_type(self, parent_arg, stmt, ignore_list = []):
        type_body = []
        file_indent = '\t'
        indent = '\t' * 2
        body_indent = '\t' * 3
        complex_type = parent_arg + 'Type'
        type_body.append(file_indent + '<xsd:complexType name="' + complex_type + '">')
        type_body.append(indent + '<xsd:all>')

        #generate choice type
        for choice in search(stmt, "choice"):
            if hasattr(choice, 'i_uses'):
                # grouping contain choice, use grouping to avoid duplicate code
                choice_type = normalize(choice.i_uses[0].arg.replace(':','_') + choice.arg)
                if choice_type not in self.ucg:
                    self.ucg.add(choice_type)
                    self.generate_choicetype(choice_type, choice)
                type_body.append(body_indent + '<xsd:element name="' + choice.arg + '" type="' + choice_type + 'Type" />')
            else:
                choice_type = normalize(stmt.arg+choice.arg)
                self.generate_choicetype(choice_type, choice)
                type_body.append(body_indent + '<xsd:element name="' + choice.arg + '" type="' + choice_type + 'Type" />')

        for property in search(stmt, list(yangelement_stmts|{'anyxml'})):
            if property.arg in ignore_list:
                continue
            if property.parent.arg != stmt.arg:
                continue
            if property.keyword == "leaf":
                type = search_one(property, 'type')
                type_arg = self.get_build_in_type(property).arg
                if type_arg in yang_to_xsd_types:
                    leaf_type_name = yang_to_xsd_types[type_arg]
                elif type.arg == "enumeration":
                    # generate enumeration type
                    self.generate_enumtype(property.arg, type)
                    leaf_type_name = normalize(property.arg)+'Type'
                elif type_arg in ("union", "type"):
                    leaf_type_name = type.arg
                else:
                    leaf_type_name = normalize(type.arg)+"Type"

                type_body.append(body_indent + '<xsd:element name="' + property.arg + '" type="' + leaf_type_name + '"/>')

            if property.keyword in ["list","container"]:
                if hasattr(property, 'i_uses'):
                    # grouping contain list, use grouping to avoid duplicate code
                    group_stmt = property.i_uses[0].i_grouping
                    if len(group_stmt.i_children) == 1 and group_stmt.i_children[0].keyword == "list":
                        leaf_type_name = normalize(group_stmt.arg)+ "Type"
                        if property.keyword == 'list':
                            type_body.append(body_indent + '<xsd:element name="' + property.arg + '" type="' + leaf_type_name + '" maxOccurs="unbounded"/>')
                        else:
                            type_body.append(body_indent + '<xsd:element name="' + property.arg + '" type="' + leaf_type_name + '"/>')
                        continue
                property_arg = property.arg
                if stmt.keyword in ["input", "output"]:
                    typename = normalize(parent_arg+"-"+property_arg)
                else:
                    typename = normalize(stmt.arg+"-"+property_arg)
                self.generate_type(typename, property)
                list_type_name = typename + "Type"
                if property.keyword == 'list':
                    type_body.append(body_indent + '<xsd:element name="' + property.arg + '" type="' + list_type_name + '" maxOccurs="unbounded"/>')
                else:
                    type_body.append(body_indent + '<xsd:element name="' + property.arg + '" type="' + list_type_name + '"/>')

            if property.keyword == "leaf-list":
                type = search_one(property, 'type')
                type_arg = self.get_build_in_type(property).arg
                if type_arg in yang_to_xsd_types:
                    type_name = yang_to_xsd_types[type_arg]
                elif type.arg == "enumeration":
                    # generate enumeration type
                    self.generate_enumtype(property.arg, type)
                    type_name = normalize(property.arg)+'Type'
                elif type_arg in ("union", "type"):
                    type_name = type.arg
                else:
                    type_name = normalize(type.arg)+"Type"

                type_body.append(body_indent+'<xsd:element name="'+property.arg+'" type="'+type_name+'" maxOccurs="unbounded" />')

            if property.keyword == "anyxml":
                type_body.append(body_indent+'<xsd:element name="'+property.arg+'" type="xsd:string" />')


        type_body.append(indent + '</xsd:all>')
        type_body.append(file_indent + '</xsd:complexType>')

        subtype = JavaValue(type_body)
        self.java_class.add_xsd_type(complex_type, subtype)

    def get_build_in_type(self, stmt):
        """Returns the built in type that stmt is derived from"""
        if stmt.keyword == 'type' and stmt.arg == 'union':
            return stmt
        type_stmt = search_one(stmt, 'type')
        if type_stmt is None:
            return stmt
        if type_stmt.arg == 'leafref':
            ref_node=type_stmt.parent.i_leafref.i_target_node
            return self.get_build_in_type(ref_node)
        try:
            typedef = type_stmt.i_typedef
        except AttributeError:
            return type_stmt
        else:
            if typedef is not None:
                return self.get_build_in_type(typedef)
            else:
                return type_stmt

    def check_stmt_entity(self, stmt):
        uses_stmt = search_one(stmt, 'uses')
        if uses_stmt:
            if uses_stmt.arg == "csp:entity":
                return True
            elif uses_stmt.i_grouping:
                for sub in uses_stmt.i_grouping.substmts:
                    if sub.arg == "csp:entity":
                        return True

        if search_one(stmt, ('csp-common', 'vertex')):
            return True

        return False

    def generate_xsd_elements(self, stmt):
        file_indent = '\t'
        indent = '\t' * 2
        body_indent = '\t' * 3

        exact = []
        stmt_arg = stmt.arg

        if self.check_stmt_entity(stmt):
            element = '<xsd:element name="'+stmt.arg+'" type="ifmap:IdentityType" />'
            exact.append(file_indent + element)
            desc = search_one(stmt, "description")
            if desc:
                exact.append(file_indent+ "<!--#IFMAP-SEMANTICS-IDL Identity('"+stmt.arg+"', \""+desc.arg+"\") -->")

            for sub_stmt in search(stmt, list(yangelement_stmts | {'anyxml'})):
                if sub_stmt.i_orig_module.arg != stmt.i_orig_module.arg and sub_stmt.i_orig_module.arg == "csp-common":
                    continue

                sub_stmt_arg = sub_stmt.arg

                edge = search_one(sub_stmt, ('csp-common', 'has-edge'))
                ref_edge = search_one(sub_stmt, ('csp-common', 'ref-edge'))
                desc = search_one(sub_stmt, "description")

                # generate link of entity
                if edge or ref_edge:
                    if edge:
                        link_type = "['has']"
                    elif ref_edge:
                        link_type = "['ref']"

                    link_name = stmt.arg+'-'+sub_stmt.arg

                    if sub_stmt.keyword == "list":
                        #If sub_stmt only have one child and child is uuid, don't generate type for sub_stmt
                        if len(sub_stmt.i_children) == 1 and sub_stmt.i_children[0].arg == "uuid" and sub_stmt.i_children[0].i_is_key:
                            element = '<xsd:element name="'+link_name+'" />'
                            exact.append(file_indent + element)
                        else:
                            generate_type = self.check_reflink(sub_stmt)
                            if generate_type:
                                ignore_list = self.get_ignorelist(sub_stmt)
                                parent_arg = normalize(stmt_arg + "-"+sub_stmt_arg)
                                self.generate_type(parent_arg, sub_stmt, ignore_list)
                                element = '<xsd:element name="'+link_name+'" type="'+parent_arg+'Type" />'
                            else:
                                element = '<xsd:element name="'+link_name+'" />'
                            exact.append(file_indent + element)

                    elif sub_stmt.keyword in ("leaf"):
                        element = '<xsd:element name="'+link_name+'" />'
                        exact.append(file_indent + element)

                    if "_refs" in sub_stmt_arg:
                        sub_stmt_name = sub_stmt_arg[:-5]
                    else:
                        sub_stmt_name = sub_stmt_arg

                    if desc:
                        exact.append(file_indent+ "<!--#IFMAP-SEMANTICS-IDL Link('"+link_name+"',"+"'"+stmt.arg+"', '"+sub_stmt_name+"', "+link_type+", \""+desc.arg+"\") -->")
                    else:
                        exact.append(file_indent+ "<!--#IFMAP-SEMANTICS-IDL Link('"+link_name+"',"+"'"+stmt.arg+"', '"+sub_stmt_name+"', "+link_type+") -->")

                    continue
                # generate property of entity
                elif sub_stmt.keyword == "container":
                    # generate complexType for container
                    parent_arg = normalize(stmt_arg + "-"+sub_stmt_arg)
                    self.generate_type(parent_arg, sub_stmt)
                    property_name = stmt_arg+"-"+sub_stmt_arg
                    if not edge and not ref_edge:
                        exact.append(file_indent+'<xsd:element name="'+property_name+'" type="'+parent_arg+'Type" />')
                elif sub_stmt.keyword == "list":
                    # generate complexType for list
                    parent_arg = normalize(stmt_arg + "-"+sub_stmt_arg)
                    self.generate_type(parent_arg, sub_stmt)
                    property_name = stmt_arg+"-"+sub_stmt_arg
                    if not edge and not ref_edge:
                        exact.append(file_indent+'<xsd:element name="'+property_name+'" type="'+parent_arg+'Type" maxOccurs="unbounded"/>')
                elif sub_stmt.keyword  == "leaf" and sub_stmt_arg != 'name':
                    type = search_one(sub_stmt, 'type')
                    property_name = stmt.arg+"-"+sub_stmt_arg
                    type_arg = self.get_build_in_type(sub_stmt).arg
                    if type_arg in yang_to_xsd_types:
                        type_name = yang_to_xsd_types[type_arg]
                    elif type.arg == "enumeration":
                        # generate enumeration type
                        self.generate_enumtype(property_name, type)
                        type_name = normalize(property_name)+'Type'
                    elif type_arg in ("union", "type"):
                        type_name = type.arg
                    else:
                        type_name = normalize(type.arg)+"Type"
                    exact.append(file_indent+'<xsd:element name="'+property_name+'" type="'+type_name+'" />')
                elif sub_stmt.keyword == "leaf-list":
                    type = search_one(sub_stmt, 'type')
                    property_name = stmt.arg+"-"+sub_stmt_arg
                    type_arg = self.get_build_in_type(sub_stmt).arg
                    if type_arg in yang_to_xsd_types:
                        type_name = yang_to_xsd_types[type_arg]
                    elif type.arg == "enumeration":
                        # generate enumeration type
                        self.generate_enumtype(property_name, type)
                        type_name = "xsd:string"
                    elif type_arg in ("union", "type"):
                        type_name = type.arg
                    else:
                        type_name = normalize(type.arg)+"Type"
                    exact.append(file_indent+'<xsd:element name="'+property_name+'" type="'+type_name+'" maxOccurs="unbounded" />')
                elif sub_stmt.keyword == "anyxml":
                    property_name = stmt.arg+"-"+sub_stmt_arg
                    exact.append(file_indent+'<xsd:element name="'+property_name+'" type="xsd:string" />')

                if sub_stmt_arg != 'name':
                    if desc:
                        exact.append(file_indent+"<!--#IFMAP-SEMANTICS-IDL Property('"+property_name+"', '"+stmt.arg+"',\""+desc.arg+"\") -->")
                    else:
                        exact.append(file_indent+"<!--#IFMAP-SEMANTICS-IDL Property('"+property_name+"', '"+stmt.arg+"') -->")

        if stmt.keyword == "rpc":
            rpc_name = stmt.arg
            element = '<xsd:element name="'+rpc_name+'" type="yang:RPC" />'
            exact.append(file_indent + element)
            for sub in stmt.i_children:
                if sub.keyword == "input":
                    input_name = normalize(rpc_name)+"_Input"
                    self.generate_type(input_name, sub)
                    input_type = rpc_name + "-input"
                    property_name = input_type + "-input"
                    exact.append(file_indent+'<xsd:element name="'+input_type+'" type="yang:RpcInputType" />')
                    exact.append(file_indent+'<xsd:element name="'+property_name+'" type="'+input_name+'Type" />')
                    exact.append(file_indent+"<!--#IFMAP-SEMANTICS-IDL Property('"+property_name+"', '"+input_type+"') -->")
                elif sub.keyword == "output":
                    output_name = normalize(rpc_name)+"_Output"
                    self.generate_type(output_name, sub)
                    output_type = rpc_name + "-output"
                    property_name = output_type + "-output"
                    exact.append(file_indent+'<xsd:element name="'+output_type+'" type="yang:RpcOutputType" />')
                    exact.append(file_indent+'<xsd:element name="'+property_name+'" type="'+output_name+'Type" />')
                    exact.append(file_indent+"<!--#IFMAP-SEMANTICS-IDL Property('"+property_name+"', '"+output_type+"') -->")
        if stmt.keyword == "notification":
            notification_name = normalize(stmt.arg) + "_Notification"
            self.generate_type(notification_name, stmt) 

        res = JavaValue(exact)
        self.java_class.append_access_method("f_routing", res)

    def generate_enumtype(self, property_name, type):
        complex_type = []

        file_indent = '\t'
        indent = '\t' * 2
        body_indent = '\t' * 3

        complex_type.append(file_indent+'<xsd:simpleType name="'+normalize(property_name)+'Type" >')
        complex_type.append(indent+'<xsd:restriction base="xsd:string">')
        for enum in type.substmts:
            complex_type.append(body_indent+'<xsd:enumeration value="'+enum.arg+'" />')
        complex_type.append(indent+'</xsd:restriction>')
        complex_type.append(file_indent+'</xsd:simpleType>')

        complextype = JavaValue(complex_type)
        self.java_class.add_xsd_type("enum_"+property_name, complextype)


    def check_reflink(self, stmt):
        generate_type = False
        for sub in stmt.i_children:
            if hasattr(sub, "i_uses"):
                if sub.i_uses[0].arg == "csp:RefLink":
                    generate_type = False
                else:
                    generate_type = True
            elif sub.arg == "uuid":
                generate_type = False
            else:
                generate_type = True

        return generate_type

    def get_ignorelist(self, stmt):
        ignore_list = []
        for sub in stmt.i_children:
            if hasattr(sub, "i_uses"):
                if sub.i_uses[0].arg == "csp:RefLink":
                    ignore_list.append(sub.arg)
            elif sub.arg == "uuid":
                ignore_list.append(sub.arg)

        return ignore_list

    def write_to_file(self):
        write_file(self.path,
                   self.filename,
                   self.java_class.as_list(),
                   self.ctx)

class JavaClass(object):
    """Encapsulates package name, imports, class declaration, constructors,
    fields, access methods, etc. for a Java Class. Also includes javadoc
    documentation where applicable.

    Implementation: Unless the 'body' attribute is used, different kind of
    methods and fields are stored in separate dictionaries so that the order of
    them in the generated class does not depend on the order in which they were
    added.

    """

    def __init__(self, filename=None, package=None, imports=None,
                 description=None, body=None, version='1.0',
                 superclass=None, interfaces=None, source='<unknown>.yang', implement=False):
        """Constructor.

        filename    -- Should preferably not contain a complete path since it is
                       displayed in a Java comment in the beginning of the code.
        package     -- Should be just the name of the package in which the class
                       will be included.
        imports     -- Should be a list of names of imported libraries.
        description -- Defines the class semantics.
        body        -- Should contain the actual code of the class if it is not
                       supplied through the add-methods
        version     -- Version number, defaults to '1.0'.
        superclass  -- Parent class of this Java class, or None
        interaces   -- List of interfaces implemented by this Java class
        source      -- A string somehow representing the origin of the class

        """
        if imports is None:
            imports = []
        self.filename = filename
        self.imports = OrderedSet()
        for i in range(len(imports)):
            self.imports.add(imports[i])
        self.description = description
        self.body = None
        self.version = version
        self.superclass = superclass
        self.interfaces = interfaces
        if interfaces is None:
            self.interfaces = []
        self.source = source
        self.xsdtypes = collections.OrderedDict()
        self.access_methods = collections.OrderedDict()
        self.support_methods = OrderedSet()
        self.attrs = [self.xsdtypes, self.access_methods]


    def add_xsd_type(self, key, xsdtype):
        """Adds a field represented as a string"""
        self.xsdtypes[key] = xsdtype


    def append_access_method(self, key, access_method):
        """Adds an access method represented as a string"""
        if self.access_methods.get(key) is None:
            self.access_methods[key] = []
        self.access_methods[key].append(access_method)

    def add_support_method(self, support_method):
        """Adds a support method represented as a string"""
        self.support_methods.add(support_method)

    def get_body(self):
        """Returns self.body. If it is None, fields and methods are added to it
        before it is returned."""
        if self.body is None:
            self.body = []
            # if self.superclass is not None or 'Serializable' in self.interfaces:
            #     self.body.extend(JavaValue(
            #         modifiers=['private', 'static', 'final', 'long'],
            #         name='serialVersionUID', value='1L').as_list())
            #     self.body.append('')
            #for method in flatten(collections.OrderedDict(sorted(self.attrs[0].items(), key=lambda t: t[0]))):
            for method in flatten(self.attrs):
                 if hasattr(method, 'as_list'):
                     self.body.extend(method.as_list())
                 else:
                     self.body.append(method)
            self.body.append("</xsd:schema>")
        return self.body

    def get_superclass_and_interfaces(self):
        """Returns a string with extends and implements"""
        res = []
        if self.superclass:
            res.append(' extends ')
            res.append(self.superclass)
        if self.interfaces:
            res.append(' implements ')
            res.append(', '.join(self.interfaces))
        return ''.join(res)

    def as_list(self):
        """Returns a string representing complete Java code for this class.

        It is vital that either self.body contains the complete code body of
        the class being generated, or that it is None and methods have been
        added using the JavaClass.add methods prior to calling this method.
        Otherwise the class will be empty.

        The class name is the filename without the file extension.

        """
        # The header is placed in the beginning of the Java file
        header = []
        header.append('<!--')
        header.append(' Copyright (c) 2013 Juniper Networks, Inc. All rights reserved.')
        header.append(' -->')

        header.append('<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema"')
        header.append('\txmlns:ifmap="http://www.trustedcomputinggroup.org/2010/IFMAP/2"')
        header.append('\txmlns:meta="http://www.trustedcomputinggroup.org/2010/IFMAP-METADATA/2">')
        header.append('')

        return header  + self.get_body()


class JavaValue(object):
    """A Java value, typically representing a field or a method in a Java
    class and optionally a javadoc comment.

    A JavaValue can have its attributes set using the optional keyword
    arguments of the constructor, or by using the add and set methods.

    Each instance of this class has an 'as_list' method which is used to get a
    list of strings representing lines of code that can be written to a Java
    file once all the attributes have been set set.

    """

    def __init__(self, exact=None, javadocs=None, modifiers=None, name=None,
                 value=None, imports=None, indent=4):
        """Initializes the attributes of a new Java value.

        Keyword arguments:
        exact (String list)     -- If supplied, the 'as_list' method will
                                   return this list until something has been
                                   added to this Java value.
        javadocs (String list)  -- A list of the lines in the javadoc
                                   declaration for this Java Value.
        modifiers (String list) -- A list of Java modifiers such as 'public'
                                   and 'static'. Also used to specify the type
                                   of fields.
        name (String)           -- The identifier used for this value.
        value (String)          -- Not used by methods, this is placed after
                                   the assignment operator in a field
                                   declaration.
        imports (String list)   -- A (possibly redundant) set of classes to
                                   import in the class declaring this value.
                                   This list is typically filtered by other
                                   classes so nothing gets imported unless it
                                   is required to for the Java class to
                                   compile.
        indent (Integer)        -- The indentation in the 'as_list'
                                   representation. Defaults to 4 spaces.

        """
        self.value = value
        self.indent = ' ' * indent
        self.default_modifiers = True

        self.javadocs = OrderedSet()
        if javadocs is not None:
            for javadoc in javadocs:
                self.add_javadoc(javadoc)

        self.modifiers = []
        if modifiers is not None:
            for modifier in modifiers:
                self.add_modifier(modifier)

        self.name = None
        if name is not None:
            self.set_name(name)

        self.imports = set([])
        if imports is not None:
            for import_ in imports:
                self.imports.add(import_)

        self.exact = exact
        self.default_modifiers = True

    def __eq__(self, other):
        """Returns True iff self and other represents an identical value"""
        for attr, value in vars(self).items():
            try:
                if getattr(other, attr) != value:
                    return False
            except AttributeError:
                return False
        return True

    def __ne__(self, other):
        """Returns True iff self and other represents different values"""
        return not self.__eq__(other)

    def _set_instance_data(self, attr, value):
        """Adds or assigns value to the attr attribute of this Java value.

        attr (String) -- The attribute to add or assign value to. If this Java
                         value does not have an attribute with this name, a
                         warning is printed with the msg "Unknown attribute"
                         followed by the attribute name. The value is added,
                         appended or assigned, depending on if the attribute is
                         a MutableSet, a list or something else, respectively.
        value         -- Typically a String, but can be anything, really.

        The 'exact' cache is invalidated is the attribute exists.

        """
        try:
            data = getattr(self, attr)
            if isinstance(data, list):
                data.append(value)
            elif isinstance(data, collections.MutableSet):
                data.add(value)
            else:
                setattr(self, attr, value)
        except AttributeError:
            print_warning(msg='Unknown attribute: ' + attr, key=attr)
        else:
            self.exact = None  # Invalidate cache

    def set_name(self, name):
        """Sets the identifier of this value"""
        self._set_instance_data('name', name)

    def set_indent(self, indent=4):
        """Sets indentation used in the as_list methods"""
        self._set_instance_data('indent', ' ' * indent)

    def add_modifier(self, modifier):
        """Adds modifier to end of list of modifiers. Overwrites modifiers set
        in constructor the first time it is invoked, to enable subclasses to
        have default modifiers.

        """
        if self.default_modifiers:
            self.modifiers = []
            self.default_modifiers = False
        self._set_instance_data('modifiers', modifier)

    def add_javadoc(self, line):
        """Adds line to javadoc comment, leading ' ', '*' and '/' removed"""
        self._set_instance_data('javadocs', line.lstrip(' */'))

    def add_dependency(self, import_):
        """Adds import_ to list of imports needed for value to compile."""
        _, sep, class_name = import_.rpartition('.')
        if sep:
            if class_name not in java_built_in:
                self.imports.add(import_)
                return class_name
        elif not any(x in java_built_in for x in (import_, import_[:-2])):
            self.imports.add(import_)
        return import_

    def javadoc_as_string(self):
        """Returns a list representing javadoc lines for this value"""
        lines = []
        if self.javadocs:
            lines.append(self.indent + '/**')
            lines.extend([self.indent + ' * ' + line for line in self.javadocs])
            lines.append(self.indent + ' */')
        return lines

    def as_list(self):
        """String list of code lines that this Java value consists of"""
        if self.exact is None:
            assert self.name is not None
            assert self.indent is not None
            self.exact = self.javadoc_as_string()
            declaration = self.modifiers + [self.name]
            if self.value is not None:
                declaration.append('=')
                declaration.append(self.value)
            self.exact.append(''.join([self.indent, ' '.join(declaration), ';']))
        return self.exact


class JavaMethod(JavaValue):
    """A Java method. Default behaviour is public void."""

    def __init__(self, exact=None, javadocs=None, modifiers=None,
                 return_type=None, name=None, params=None, exceptions=None,
                 body=None, indent=4):
        """Initializes the attributes of a new Java method.

        Keyword arguments:
        exact (String list)     -- If supplied, the 'as_list' method will
                                   return this list until something has been
                                   added to this Java value.
        javadocs (String list)  -- A list of the lines in the javadoc
                                   declaration for this Java Value.
        modifiers (String list) -- A list of Java modifiers such as 'public'
                                   and 'static'. Also used to specify the type
                                   of fields.
        return_type (String)    -- The return type of the method. To avoid
                                   adding the type as a required import,
                                   assign to the return_type attribute directly
                                   instead of using this argument.
        name (String)           -- The identifier used for this value.
        params (str tuple list) -- A list of 2-tuples representing the type and
                                   name of the parameters of this method. To
                                   avoid adding the type as a required import,
                                   assign to the parameters attribute directly
                                   instead of using this argument.
        exceptions (str list)   -- A list of exceptions thrown by the method.
        value (String)          -- Not used by methods, this is placed after
                                   the assignment operator in a field
                                   declaration.
        imports (String list)   -- A (possibly redundant) set of classes to
                                   import in the class declaring this value.
                                   This list is typically filtered by other
                                   classes so nothing gets imported unless it
                                   is required to for the Java class to
                                   compile.
        indent (Integer)        -- The indentation in the 'as_list'
                                   representation. Defaults to 4 spaces.

        """
        super(JavaMethod, self).__init__(exact=exact, javadocs=javadocs,
                                         modifiers=modifiers, name=name,
                                         value=None, indent=indent)
        if self.modifiers == []:
            self.add_modifier('public')

        self.return_type = 'void'
        if return_type is not None:
            self.set_return_type(return_type)

        self.parameters = OrderedSet()
        if params is not None:
            for param_type, param_name in params:
                self.add_parameter(param_type, param_name)

        self.exceptions = OrderedSet()
        if exceptions is not None:
            for exc in exceptions:
                self.add_exception(exc)

        self.body = []
        if body is not None:
            for line in body:
                self.add_line(line)

        self.exact = exact
        self.default_modifiers = True

    def set_return_type(self, return_type):
        """Sets the type of the return value of this method"""
        retval = None if not return_type else self.add_dependency(return_type)
        self._set_instance_data('return_type', retval)

    def add_parameter(self, param_type, param_name):
        """Adds a parameter to this method. The argument type is added to list
        of dependencies.

        param_type -- String representation of the argument type
        param_name -- String representation of the argument name
        """
        self._set_instance_data('parameters',
                                ' '.join([self.add_dependency(param_type),
                                          param_name]))

    def add_exception(self, exception):
        """Adds exception to method"""
        self._set_instance_data('exceptions',
                                self.add_dependency(exception))

    def add_line(self, line):
        """Adds line to method body"""
        self._set_instance_data('body', self.indent + ' ' * 4 + line)

    def as_list(self):
        """String list of code lines that this Java method consists of.
        Overrides JavaValue.as_list().

        """
        MAX_COLS = 80
        if self.exact is None:
            assert self.name is not None
            assert self.indent is not None
            self.exact = self.javadoc_as_string()
            header = self.modifiers[:]
            if self.return_type is not None:
                header.append(self.return_type)
            header.append(self.name)
            signature = [self.indent]
            signature.append(' '.join(header))
            signature.append('(')
            signature.append(', '.join(self.parameters))
            signature.append(')')
            if self.exceptions:
                signature.append(' throws ')
                signature.append(', '.join(self.exceptions))
                if sum(len(s) for s in signature) >= MAX_COLS:
                    signature.insert(-2, '\n' + (self.indent * 3)[:-1])
            signature.append(' {')
            self.exact.append(''.join(signature))
            self.exact.extend(self.body)
            self.exact.append(self.indent + '}')
        return self.exact

class OrderedSet(collections.MutableSet):
    """A set of unique items that preserves the insertion order.

    Created by: Raymond Hettinger 2009-03-19
    Edited by: Emil Wall 2012-08-03
    Licence: http://opensource.org/licenses/MIT
    Original source: http://code.activestate.com/recipes/576694/

    An ordered set is implemented as a wrapper class for a dictionary
    implementing a doubly linked list. It also has a pointer to the last item
    in the set (self.end) which is used by the add and _iterate methods to add
    items to the end of the list and to know when an iteration has finished,
    respectively.

    """

    def __init__(self, iterable=None):
        """Creates an ordered set.

        iterable -- A mutable iterable, typically a list or a set, containing
                    initial values of the set. If the default value (None) is
                    used, the set is initialized as empty.

        """
        self.ITEM, self.PREV, self.NEXT = list(range(3))
        self.end = end = []
        end += [None, end, end]         # sentinel node for doubly linked list
        self.map = {}                   # value --> [value, prev, next]
        if iterable is not None:
            self |= iterable

    def __len__(self):
        """Returns the number of items in this set."""
        return len(self.map)

    def __contains__(self, item):
        """Returns true if item is in this set; false otherwise."""
        return item in self.map

    def add(self, item):
        """Adds an item to the end of this set."""
        if item not in self:
            self.map[item] = [item, self.end[self.PREV], self.end]
            self.end[self.PREV][self.NEXT] = self.map[item]
            self.end[self.PREV] = self.map[item]

    def add_first(self, item):
        """Adds an item to the beginning of this set."""
        if item not in self:
            self.map[item] = [item, self.end, self.end[self.NEXT]]
            self.end[self.NEXT][self.PREV] = self.map[item]
            self.end[self.NEXT] = self.map[item]

    def discard(self, item):
        """Finds and discards an item from this set, amortized O(1) time."""
        if item in self:
            item, prev, after = self.map.pop(item)
            prev[self.NEXT] = after
            after[self.PREV] = prev

    def _iterate(self, iter_index):
        """Internal generator method to iterate through this set.

        iter_index -- If 1, the set is iterated in reverse order. If 2, the set
                      is iterated in order of insertion. Else IndexError.

        """
        curr = self.end[iter_index]
        while curr is not self.end:
            yield curr[self.ITEM]
            curr = curr[iter_index]

    def __iter__(self):
        """Returns a generator object for iterating the set in the same order
        as its items were added.

        """
        return self._iterate(self.NEXT)

    def __reversed__(self):
        """Returns a generator object for iterating the set, beginning with the
        most recently added item and ending with the first/oldest item.

        """
        return self._iterate(self.PREV)

    def pop(self, last=True):
        """Discards the first or last item of this set.

        last -- If True the last item is discarded, otherwise the first.

        """
        if not self:
            raise KeyError('set is empty')
        item = next(reversed(self)) if last else next(iter(self))
        self.discard(item)
        return item

    def as_sorted_list(self):
        """Returns a sorted list with the items in this set"""
        res = [x for x in self]
        res.sort()
        return res

    def __repr__(self):
        """Returns a string representing this set. If empty, the string
        returned is 'OrderedSet()', otherwise if the set contains items a, b
        and c: 'OrderedSet([a, b, c])'

        """
        if not self:
            return '%s()' % (self.__class__.__name__,)
        return '%s(%r)' % (self.__class__.__name__, list(self))

    def __eq__(self, other):
        """Returns True if other is an OrderedSet containing the same items as
        other, in the same order.

        """
        return isinstance(other, OrderedSet) and list(self) == list(other)

    def __del__(self):
        """Destructor, clears self to avoid circular reference which could
        otherwise occur due to the doubly linked list.

        """
        self.clear()
