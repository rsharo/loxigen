# Copyright 2013, Big Switch Networks, Inc.
#
# LoxiGen is licensed under the Eclipse Public License, version 1.0 (EPL), with
# the following special exception:
#
# LOXI Exception
#
# As a special exception to the terms of the EPL, you may distribute libraries
# generated by LoxiGen (LoxiGen Libraries) under the terms of your choice, provided
# that copyright and licensing notices generated by LoxiGen are not altered or removed
# from the LoxiGen Libraries and the notice provided below is (i) included in
# the LoxiGen Libraries, if distributed in source code form and (ii) included in any
# documentation for the LoxiGen Libraries, if distributed in binary form.
#
# Notice: "Copyright 2013, Big Switch Networks, Inc. This library was generated by the LoxiGen Compiler."
#
# You may not use this file except in compliance with the EPL or LOXI Exception. You may obtain
# a copy of the EPL at:
#
# http://www.eclipse.org/legal/epl-v10.html
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# EPL for the specific language governing permissions and limitations
# under the EPL.

# Prototype of an Intermediate Object model for the java code generator
# A lot of this stuff could/should probably be merged with the python utilities

import collections
from collections import namedtuple, defaultdict, OrderedDict
import logging
import os
import pdb
import re

from generic_utils import find, memoize, OrderedSet, OrderedDefaultDict
import of_g
from loxi_ir import *
import loxi_front_end.type_maps as type_maps
import loxi_utils.loxi_utils as loxi_utils
import py_gen.util as py_utils
import test_data

import java_gen.java_type as java_type
from java_gen.java_type import erase_type_annotation

class JavaModel(object):
    enum_blacklist = set(("OFDefinitions",))
    enum_entry_blacklist = defaultdict(lambda: set(), OFFlowWildcards=set([ "NW_DST_BITS", "NW_SRC_BITS", "NW_SRC_SHIFT", "NW_DST_SHIFT" ]))
    # OFUint structs are there for god-knows what in loci. We certainly don't need them.
    interface_blacklist = set( ("OFUint8", "OFUint32",))
    write_blacklist = defaultdict(lambda: set(), OFOxm=set(('typeLen',)), OFAction=set(('type',)), OFInstruction=set(('type',)), OFFlowMod=set(('command', )))
    virtual_interfaces = set(['OFOxm', 'OFInstruction', 'OFFlowMod', 'OFBsnVport' ])

    OxmMapEntry = namedtuple("OxmMapEntry", ["type_name", "value", "masked" ])
    oxm_map = { "OFOxmInPortMasked": OxmMapEntry("OFPort", "IN_PORT", True) }

    @property
    @memoize
    def versions(self):
        return OrderedSet( JavaOFVersion(raw_version) for raw_version in of_g.target_version_list )

    @property
    @memoize
    def interfaces(self):
        version_map_per_class = collections.OrderedDict()

        for raw_version, of_protocol in of_g.ir.items():
            jversion = JavaOFVersion(of_protocol.wire_version)

            for of_class in of_protocol.classes:
                if not of_class.name in version_map_per_class:
                    version_map_per_class[of_class.name] = collections.OrderedDict()

                version_map_per_class[of_class.name][jversion] = of_class

        interfaces = []
        for class_name, version_map in version_map_per_class.items():
            interfaces.append(JavaOFInterface(class_name, version_map))

        interfaces = [ i for i in interfaces if i.name not in self.interface_blacklist ]

        return interfaces

    @memoize
    def interface_by_name(self, name):
        return find(lambda i: erase_type_annotation(i.name) == erase_type_annotation(name), self.interfaces)

    @property
    @memoize
    def all_classes(self):
        return [clazz for interface in self.interfaces for clazz in interface.versioned_classes]

    @property
    @memoize
    def enums(self):
        name_version_enum_map = OrderedDefaultDict(lambda: OrderedDict())

        for version in self.versions:
            of_protocol = of_g.ir[version.int_version]
            for enum in of_protocol.enums:
                name_version_enum_map[enum.name][version] = enum

        enums = [ JavaEnum(name, version_enum_map) for name, version_enum_map,
                        in name_version_enum_map.items() ]

        # inelegant - need java name here
        enums = [ enum for enum in enums if enum.name not in self.enum_blacklist ]
        return enums

    @memoize
    def enum_by_name(self, name):
        res = find(lambda e: e.name == name, self.enums)
        if not res:
            raise KeyError("Could not find enum with name %s" % name)
        return res

    @property
    @memoize
    def of_factories(self):
        prefix = "org.openflow.protocol"

        factories = OrderedDict()

        sub_factory_classes = ("OFAction", "OFInstruction", "OFMeterBand", "OFOxm", "OFQueueProp")
        for base_class in sub_factory_classes:
            package = base_class[2:].lower()
            remove_prefix = base_class[2].lower() + base_class[3:]

            # HACK need to have a better way to deal with parameterized base classes
            annotated_base_class = base_class + "<?>" if base_class == "OFOxm" else base_class

            factories[base_class] = OFFactory(package="%s.%s" % (prefix, package),
                    name=base_class + "s", members=[], remove_prefix=remove_prefix, base_class=annotated_base_class, sub_factories={})

        factories[""] = OFFactory(
                    package=prefix,
                    name="OFFactory",
                    remove_prefix="",
                    members=[], base_class="OFMessage", sub_factories=OrderedDict(
                        ("{}{}s".format(n[2].lower(), n[3:]), "{}s".format(n)) for n in sub_factory_classes ))

        for i in self.interfaces:
            for n, factory in factories.items():
                if n == "":
                    factory.members.append(i)
                    break
                else:
                    super_class = self.interface_by_name(n)
                    if i.is_instance_of(super_class):
                        factory.members.append(i)
                        break
        return factories.values()

    def generate_class(self, clazz):
        """ return wether or not to generate implementation class clazz.
            Now true for everything except OFTableModVer10.
            @param clazz JavaOFClass instance
        """
        if clazz.interface.name.startswith("OFMatchV"):
            return True
        elif clazz.name == "OFTableModVer10":
            # tablemod ver 10 is a hack and has no oftype defined
            return False
        if loxi_utils.class_is_message(clazz.interface.c_name):
            return True
        if loxi_utils.class_is_oxm(clazz.interface.c_name):
            return True
        if loxi_utils.class_is_action(clazz.interface.c_name):
            return True
        if loxi_utils.class_is_instruction(clazz.interface.c_name):
            return True
        else:
            return True


class OFFactory(namedtuple("OFFactory", ("package", "name", "members", "remove_prefix", "base_class", "sub_factories"))):
    @property
    def factory_classes(self):
            return [ OFFactoryClass(
                    package="org.openflow.protocol.ver{}".format(version.of_version),
                    name="{}Ver{}".format(self.name, version.of_version),
                    interface=self,
                    version=version
                    ) for version in model.versions ]

    def method_name(self, member, builder=True):
        n = member.variable_name
        if n.startswith(self.remove_prefix):
            n = n[len(self.remove_prefix):]
            n = n[0].lower() + n[1:]
        if builder:
            return "build" + n[0].upper() + n[1:]
        else:
            return n

OFGenericClass = namedtuple("OFGenericClass", ("package", "name"))
class OFFactoryClass(namedtuple("OFFactoryClass", ("package", "name", "interface", "version"))):
    @property
    def base_class(self):
        return self.interface.base_class

    @property
    def versioned_base_class(self):
        base_class_interface = model.interface_by_name(self.interface.base_class)
        if base_class_interface and base_class_interface.has_version(self.version):
            return base_class_interface.versioned_class(self.version)
        else:
            return None

model = JavaModel()

#######################################################################
### OFVersion
#######################################################################

class JavaOFVersion(object):
    """ Models a version of OpenFlow. contains methods to convert the internal
        Loxi version to a java constant / a string """
    def __init__(self, int_version):
        self.int_version = int(int_version)

    @property
    def of_version(self):
        return "1" + str(int(self.int_version) - 1)

    @property
    def constant_version(self):
        return "OF_" + self.of_version

    def __repr__(self):
        return "JavaOFVersion(%d)" % self.int_version

    def __str__(self):
        return of_g.param_version_names[self.int_version]

    def __hash__(self):
        return hash(self.int_version)

    def __eq__(self, other):
        if other is None or type(self) != type(other):
            return False
        return (self.int_version,) == (other.int_version,)

#######################################################################
### Interface
#######################################################################

class JavaOFInterface(object):
    """ Models an OpenFlow Message class for the purpose of the java class.
        Version agnostic, in contrast to the loxi_ir python model.
    """
    def __init__(self, c_name, version_map):
        """"
        @param c_name: loxi style name (e.g., of_flow_add)
        @param version_map map of { JavaOFVersion: OFClass (from loxi_ir) }
        """
        self.c_name = c_name
        self.version_map = version_map
        # name: the Java Type name, e.g., OFFlowAdd
        self.name = java_type.name_c_to_caps_camel(c_name) if c_name != "of_header" else "OFMessage"
        # variable_name name to use for variables of this type. i.e., flowAdd
        self.variable_name = self.name[2].lower() + self.name[3:]
        self.title_name = self.variable_name[0].upper() + self.variable_name[1:]
        # name for use in constants: FLOW_ADD
        self.constant_name = c_name.upper().replace("OF_", "")

        pck_suffix, parent_interface, self.type_annotation = self.class_info()
        self.package = "org.openflow.protocol.%s" % pck_suffix if pck_suffix else "org.openflow.protocol"
        if self.name != parent_interface:
            self.parent_interface = parent_interface
        else:
            self.parent_interface = None

    def is_instance_of(self, other_class):
        if self == other_class:
            return True
        parent = self.super_class
        if parent is None:
            return False
        else:
            return parent.is_instance_of(other_class)

    @property
    def super_class(self):
        if not self.parent_interface:
            return None
        else:
            return model.interface_by_name(self.parent_interface)


    def inherited_declaration(self, type_spec="?"):
        if self.type_annotation:
            return "%s<%s>" % (self.name, type_spec)
        else:
            return "%s" % self.name

    @property
    def type_variable(self):
        if self.type_annotation:
            return "<T>"
        else:
            return "";

    def class_info(self):
        """ return tuple of (package_prefix, parent_class) for the current JavaOFInterface"""
        # FIXME: This duplicates inheritance information that is now available in the loxi_ir
        # model (note, that the loxi model is on versioned classes). Should check/infer the
        # inheritance information from the versioned lox_ir classes.
        if re.match(r'OF.+StatsRequest$', self.name):
            return ("", "OFStatsRequest", None)
        elif re.match(r'OF.+StatsReply$', self.name):
            return ("", "OFStatsReply", None)
        elif re.match(r'OFFlow(Add|Modify(Strict)?|Delete(Strict)?)$', self.name):
            return ("", "OFFlowMod", None)
        elif loxi_utils.class_is_message(self.c_name) and re.match(r'OFBsn.+$', self.name):
            return ("", "OFBsnHeader", None)
        elif loxi_utils.class_is_message(self.c_name) and re.match(r'OFNicira.+$', self.name):
            return ("", "OFNiciraHeader", None)
        elif re.match(r'OFMatch.*', self.name):
            return ("", "Match", None)
        elif loxi_utils.class_is_message(self.c_name):
            return ("", "OFMessage", None)
        elif loxi_utils.class_is_action(self.c_name):
            if re.match(r'OFActionBsn.+', self.name):
                return ("action", "OFActionBsn", None)
            elif re.match(r'OFActionNicira.+', self.name):
                return ("action", "OFActionNicira", None)
            else:
                return ("action", "OFAction", None)
        elif re.match(r'OFBsnVport.+$', self.name):
            return ("", "OFBsnVport", None)
        elif self.name == "OFOxm":
            return ("oxm", None, "T extends OFValueType<T>")
        elif loxi_utils.class_is_oxm(self.c_name):
            if self.name in model.oxm_map:
                return ("oxm", "OFOxm<%s>" % model.oxm_map[self.name].type_name, None)
            else:
                return ("oxm", "OFOxm", None)
        elif loxi_utils.class_is_instruction(self.c_name):
            return ("instruction", "OFInstruction", None)
        elif loxi_utils.class_is_meter_band(self.c_name):
            return ("meterband", "OFMeterBand", None)
        elif loxi_utils.class_is_queue_prop(self.c_name):
            return ("queueprop", "OFQueueProp", None)
        elif loxi_utils.class_is_hello_elem(self.c_name):
            return ("", "OFHelloElem", None)
        else:
            return ("", None, None)

    @property
    @memoize
    def writeable_members(self):
        return [ m for m in self.members if m.is_writeable ]

    @property
    @memoize
    def members(self):
        return self.ir_model_members + self.virtual_members

    @property
    @memoize
    def ir_model_members(self):
        """return a list of all members to be exposed by this interface. Corresponds to
           the union of the members of the vesioned classes without length, fieldlength
           and pads (those are handled automatically during (de)serialization and not exposed"""
        all_versions = []
        member_map = collections.OrderedDict()

        for (version, of_class) in self.version_map.items():
            for of_member in of_class.members:
                if isinstance(of_member, OFLengthMember) or \
                   isinstance(of_member, OFFieldLengthMember) or \
                   isinstance(of_member, OFPadMember):
                    continue
                if of_member.name not in member_map:
                    member_map[of_member.name] = JavaMember.for_of_member(self, of_member)

        return tuple(member_map.values())

    @property
    def virtual_members(self):
        if self.name == "OFOxm":
            return (
                    JavaVirtualMember(self, "value", java_type.generic_t),
                    JavaVirtualMember(self, "mask", java_type.generic_t),
                    JavaVirtualMember(self, "matchField", java_type.make_match_field_jtype("T")),
                    JavaVirtualMember(self, "masked", java_type.boolean),
                   )
        elif self.parent_interface and self.parent_interface.startswith("OFOxm"):
            field_type = java_type.make_match_field_jtype(model.oxm_map[self.name].type_name) \
                if self.name in model.oxm_map \
                else java_type.make_match_field_jtype()

            return (
                    JavaVirtualMember(self, "matchField", field_type),
                    JavaVirtualMember(self, "masked", java_type.boolean),
                   ) \
                   + \
                   (
                           ( JavaVirtualMember(self, "mask", find(lambda x: x.name == "value", self.ir_model_members).java_type), ) if not find(lambda x: x.name == "mask", self.ir_model_members) else
                    ()
                   )
        else:
            return ()

    @property
    @memoize
    def is_virtual(self):
        """ Is this interface virtual. If so, do not generate a builder interface """
        return self.name in model.virtual_interfaces or all(ir_class.virtual for ir_class in self.version_map.values())

    @property
    def is_universal(self):
        """ Is this interface universal, i.e., does it exist in all OF versions? """
        return len(self.all_versions) == len(model.versions)

    @property
    @memoize
    def all_versions(self):
        """ return list of all versions that this interface exists in """
        return self.version_map.keys()

    def has_version(self, version):
        return version in self.version_map

    def versioned_class(self, version):
        return JavaOFClass(self, version, self.version_map[version])

    @property
    @memoize
    def versioned_classes(self):
            return [ self.versioned_class(version) for version in self.all_versions ]

#######################################################################
### (Versioned) Classes
#######################################################################

class JavaOFClass(object):
    """ Models an OpenFlow Message class for the purpose of the java class.
        Version specific child of a JavaOFInterface
    """
    def __init__(self, interface, version, ir_class):
        """
        @param interface JavaOFInterface instance of the parent interface
        @param version JavaOFVersion
        @param ir_class OFClass from loxi_ir
        """
        self.interface = interface
        self.ir_class = ir_class
        self.c_name = self.ir_class.name
        self.version = version
        self.constant_name = self.c_name.upper().replace("OF_", "")
        self.package = "org.openflow.protocol.ver%s" % version.of_version
        self.generated = False

    @property
    @memoize
    def unit_test(self):
        return JavaUnitTestSet(self)

    @property
    def name(self):
        return "%sVer%s" % (self.interface.name, self.version.of_version)

    @property
    def variable_name(self):
        return self.name[3:]

    @property
    def length(self):
        if self.is_fixed_length:
            return self.min_length
        else:
            raise Exception("No fixed length for class %s, version %s" % (self.name, self.version))

    @property
    def min_length(self):
        """ @return the minimum wire length of an instance of this class in bytes """
        id_tuple = (self.ir_class.name, self.version.int_version)
        return of_g.base_length[id_tuple] if id_tuple in of_g.base_length else -1

    @property
    def is_fixed_length(self):
        """ true iff this class serializes to a fixed length on the wire """
        return (self.ir_class.name, self.version.int_version) in of_g.is_fixed_length

    def all_properties(self):
        return self.interface.members

    def get_member(self, name):
        for m in self.members:
            if m.name == name:
                return m

    @property
    @memoize
    def data_members(self):
        return [ prop for prop in self.members if prop.is_data ]

    @property
    @memoize
    def fixed_value_members(self):
        return [ prop for prop in self.members if prop.is_fixed_value ]

    @property
    @memoize
    def public_members(self):
        return [ prop for prop in self.members if prop.is_public ]

    @property
    @memoize
    def members(self):
        return self.ir_model_members + self.virtual_members

    @property
    def ir_model_members(self):
        members = [ JavaMember.for_of_member(self, of_member) for of_member in self.ir_class.members ]
        return tuple(members)

    @property
    def virtual_members(self):
        if self.interface.parent_interface and self.interface.parent_interface.startswith("OFOxm"):
            if self.interface.name in model.oxm_map:
                oxm_entry = model.oxm_map[self.interface.name]
                return (
                    JavaVirtualMember(self, "matchField", java_type.make_match_field_jtype(oxm_entry.type_name), "MatchField.%s" % oxm_entry.value),
                    JavaVirtualMember(self, "masked", java_type.boolean, "true" if oxm_entry.masked else "false"),
                   )
            else:
                return (
                    JavaVirtualMember(self, "matchField", java_type.make_match_field_jtype(), "null"),
                    JavaVirtualMember(self, "masked", java_type.boolean, "false"),
                   )
        else:
            return ()

    def all_versions(self):
        return [ JavaOFVersion(int_version)
                 for int_version in of_g.unified[self.c_name]
                 if int_version != 'union' and int_version != 'object_id' ]

    def version_is_inherited(self, version):
        return 'use_version' in of_g.unified[self.ir_class.name][version.int_version]

    def inherited_from(self, version):
        return JavaOFVersion(of_g.unified[self.ir_class.name][version.int_version]['use_version'])

    @property
    def is_virtual(self):
        return self.ir_class.virtual # type_maps.class_is_virtual(self.c_name) or self.ir_class.virtual

    @property
    def discriminator(self):
        return find(lambda m: isinstance(m, OFDiscriminatorMember), self.ir_class.members)

    @property
    def is_extension(self):
        return type_maps.message_is_extension(self.c_name, -1)

    @property
    def align(self):
        return int(self.ir_class.params['align']) if 'align' in self.ir_class.params else 0

    @property
    @memoize
    def superclass(self):
        return find(lambda c: c.version == self.version and c.c_name == self.ir_class.superclass, model.all_classes)

    @property
    @memoize
    def subclasses(self):
        return [ c for c in model.all_classes if c.version == self.version and c.ir_class.superclass == self.c_name ]

#######################################################################
### Member
#######################################################################


class JavaMember(object):
    """ Models a property (member) of an openflow class. """
    def __init__(self, msg, name, java_type, member):
        self.msg = msg
        self.name = name
        self.java_type = java_type
        self.member = member
        self.c_name = self.member.name if(hasattr(self.member, "name")) else ""

    @property
    def title_name(self):
        return self.name[0].upper() + self.name[1:]

    @property
    def constant_name(self):
        return self.c_name.upper()

    @property
    def getter_name(self):
        return ("is" if self.java_type.public_type == "boolean" else "get") + self.title_name

    @property
    def setter_name(self):
        return "set" + self.title_name

    @property
    def default_name(self):
        if self.is_fixed_value:
            return self.constant_name
        else:
            return "DEFAULT_"+self.constant_name

    @property
    def default_value(self):
        java_type = self.java_type.public_type;

        if self.is_fixed_value:
            return self.enum_value
        elif re.match(r'List.*', java_type):
            return "Collections.emptyList()"
        elif java_type == "boolean":
            return "false";
        elif java_type in ("byte", "char", "short", "int", "long"):
            return "({0}) 0".format(java_type);
        else:
            return "null";

    @property
    def enum_value(self):
        if self.name == "version":
            return "OFVersion.%s" % self.msg.version.constant_version

        java_type = self.java_type.public_type;
        try:
            global model
            enum = model.enum_by_name(java_type)
            entry = enum.entry_by_version_value(self.msg.version, self.value)
            return "%s.%s" % ( enum.name, entry.name)
        except KeyError, e:
            print e.message
            return self.value

    @property
    def is_pad(self):
        return isinstance(self.member, OFPadMember)

    def is_type_value(self, version=None):
        if(version==None):
            return any(self.is_type_value(version) for version in self.msg.all_versions)
        try:
            return self.c_name in get_type_values(self.msg.c_name, version.int_version)
        except:
            return False

    @property
    def is_field_length_value(self):
        return isinstance(self.member, OFFieldLengthMember)

    @property
    def is_discriminator(self):
        return isinstance(self.member, OFDiscriminatorMember)

    @property
    def is_length_value(self):
        return isinstance(self.member, OFLengthMember)

    @property
    def is_public(self):
        return not (self.is_pad or self.is_length_value)

    @property
    def is_data(self):
        return isinstance(self.member, OFDataMember) and self.name != "version"

    @property
    def is_fixed_value(self):
        return hasattr(self.member, "value") or self.name == "version" \
                or ( self.name == "length" and self.msg.is_fixed_length) \
                or ( self.name == "len" and self.msg.is_fixed_length)

    @property
    def value(self):
        if self.name == "version":
            return self.msg.version.int_version
        elif self.name == "length" or self.name == "len":
            return self.msg.length
        else:
            return self.java_type.format_value(self.member.value)

    @property
    def priv_value(self):
        if self.name == "version":
            return self.msg.version.int_version
        elif self.name == "length" or self.name == "len":
            return self.msg.length
        else:
            return self.java_type.format_value(self.member.value, pub_type=False)


    @property
    def is_writeable(self):
        return self.is_data and not self.name in model.write_blacklist[self.msg.name]

    def get_type_value_info(self, version):
        return get_type_values(msg.c_name, version.int_version)[self.c_name]

    @property
    def length(self):
        if hasattr(self.member, "length"):
            return self.member.length
        else:
            count, base = loxi_utils.type_dec_to_count_base(self.member.type)
            return of_g.of_base_types[base]['bytes'] * count

    @staticmethod
    def for_of_member(java_class, member):
        if isinstance(member, OFPadMember):
            return JavaMember(None, "", None, member)
        else:
            if member.name == 'len':
                name = 'length'
            elif member.name == 'value_mask':
                name = 'mask'
            else:
                name = java_type.name_c_to_camel(member.name)
            j_type = java_type.convert_to_jtype(java_class.c_name, member.name, member.oftype)
            return JavaMember(java_class, name, j_type, member)

    @property
    def is_universal(self):
        if not self.msg.c_name in of_g.unified:
            print("%s not self.unified" % self.msg.c_name)
            return False
        for version in of_g.unified[self.msg.c_name]:
            if version == 'union' or version =='object_id':
                continue
            if 'use_version' in of_g.unified[self.msg.c_name][version]:
                continue

            if not self.member.name in (f['name'] for f in of_g.unified[self.msg.c_name][version]['members']):
                return False
        return True

    @property
    def is_virtual(self):
        return False

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        if other is None or type(self) != type(other):
            return False
        return (self.name,) == (other.name,)

class JavaVirtualMember(JavaMember):
    """ Models a virtual property (member) of an openflow class that is not backed by a loxi ir member """
    def __init__(self, msg, name, java_type, value=None):
        JavaMember.__init__(self, msg, name, java_type, member=None)
        self._value = value

    @property
    def is_fixed_value(self):
        return True

    @property
    def value(self):
        return self._value

    @property
    def priv_value(self):
        return self._value


    @property
    def is_universal(self):
        return True

    @property
    def is_virtual(self):
        return True

#######################################################################
### Unit Test
#######################################################################

class JavaUnitTestSet(object):
    def __init__(self, java_class):
        self.java_class = java_class
        first_data_file_name = "of{version}/{name}.data".format(version=java_class.version.of_version,
                                                     name=java_class.c_name[3:])
        data_file_template = "of{version}/{name}.".format(version=java_class.version.of_version,
                                                     name=java_class.c_name[3:]) + "{i}.data"
        test_class_name = self.java_class.name + "Test"
        self.test_units = []
        if test_data.exists(first_data_file_name):
            self.test_units.append(JavaUnitTest(java_class, first_data_file_name, test_class_name))
        i = 1
        while test_data.exists(data_file_template.format(i=i)):
            self.test_units.append(JavaUnitTest(java_class, data_file_template.format(i=i), test_class_name + str(i)))
            i = i + 1
        
    @property
    def package(self):
        return self.java_class.package

    @property
    def has_test_data(self):
        return len(self.test_units) > 0

    @property
    def length(self):
        return len(self.test_units)
    
    def get_test_unit(self, i):
        return self.test_units[i]


class JavaUnitTest(object):
    def __init__(self, java_class, file_name=None, test_class_name=None):
        self.java_class = java_class
        if file_name is None:
            self.data_file_name = "of{version}/{name}.data".format(version=java_class.version.of_version,
                                                         name=java_class.c_name[3:])
        else:
            self.data_file_name = file_name
        if test_class_name is None:
            self.test_class_name = self.java_class.name + "Test"
        else:
            self.test_class_name = test_class_name
        
    @property
    def package(self):
        return self.java_class.package

    @property
    def name(self):
        return self.test_class_name

    @property
    def has_test_data(self):
        return test_data.exists(self.data_file_name)

    @property
    @memoize
    def test_data(self):
        return test_data.read(self.data_file_name)


#######################################################################
### Enums
#######################################################################

class JavaEnum(object):
    def __init__(self, c_name, version_enum_map):
        self.c_name = c_name

        if c_name == "of_stats_types":
            self.name = "OFStatsType"
        else:
            self.name   = "OF" + java_type.name_c_to_caps_camel("_".join(c_name.split("_")[1:]))

        # Port_features has constants that start with digits
        self.name_prefix = "PF_" if self.name == "OFPortFeatures" else ""

        self.version_enums = version_enum_map

        entry_name_version_value_map = OrderedDefaultDict(lambda: OrderedDict())
        for version, ir_enum in version_enum_map.items():
            for ir_entry in ir_enum.entries:
                if "virtual" in ir_entry.params:
                    continue
                entry_name_version_value_map[ir_entry.name][version] = ir_entry.value

        self.entries = [ JavaEnumEntry(self, name, version_value_map)
                         for (name, version_value_map) in entry_name_version_value_map.items() ]

        self.entries = [ e for e in self.entries if e.name not in model.enum_entry_blacklist[self.name] ]
        self.package = "org.openflow.protocol"

    def wire_type(self, version):
        ir_enum = self.version_enums[version]
        if "wire_type" in ir_enum.params:
            return java_type.convert_enum_wire_type_to_jtype(ir_enum.params["wire_type"])
        else:
            return java_type.u8

    @property
    def versions(self):
        return self.version_enums.keys()

    @memoize
    def entry_by_name(self, name):
        res = find(lambda e: e.name == name, self.entries)
        if res:
            return res
        else:
            raise KeyError("Enum %s: no entry with name %s" % (self.name, name))

    @memoize
    def entry_by_c_name(self, name):
        res = find(lambda e: e.c_name == name, self.entries)
        if res:
            return res
        else:
            raise KeyError("Enum %s: no entry with c_name %s" % (self.name, name))

    @memoize
    def entry_by_version_value(self, version, value):
        res = find(lambda e: e.values[version] == value if version in e.values else False, self.entries)
        if res:
            return res
        else:
            raise KeyError("Enum %s: no entry with version %s, value %s" % (self.name, version, value))

# values: Map JavaVersion->Value
class JavaEnumEntry(object):
    def __init__(self, enum, name, values):
        self.enum = enum
        self.name = enum.name_prefix + "_".join(name.split("_")[1:]).upper()
        self.values = values

    def has_value(self, version):
        return version in self.values

    def value(self, version):
        return self.values[version]

    def format_value(self, version):
        res = self.enum.wire_type(version).format_value(self.values[version])
        return res

    def all_values(self, versions, not_present=None):
        return [ self.values[version] if version in self.values else not_present for version in versions ]
