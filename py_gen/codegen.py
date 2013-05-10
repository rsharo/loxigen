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

from collections import namedtuple
import struct
import of_g
import loxi_front_end.type_maps as type_maps
import loxi_utils.loxi_utils as utils
import util
import oftype
from loxi_ir import *

PyOFClass = namedtuple('PyOFClass', ['name', 'pyname', 'members', 'type_members',
                                     'min_length', 'is_fixed_length'])

# Return the name for the generated Python class
def generate_pyname(cls):
    if utils.class_is_action(cls):
        return cls[10:]
    elif utils.class_is_oxm(cls):
        return cls[7:]
    elif utils.class_is_meter_band(cls):
        return cls[14:]
    elif utils.class_is_instruction(cls):
        return cls[15:]
    else:
        return cls[3:]

# Create intermediate representation, extended from the LOXI IR
# HACK the oftype member attribute is replaced with an OFType instance
def build_ofclasses(version):
    blacklist = ["of_experimenter", "of_action_experimenter"]
    ofclasses = []
    for ofclass in of_g.ir[version].classes:
        cls = ofclass.name
        if type_maps.class_is_virtual(cls):
            continue
        if cls in blacklist:
            continue

        members = []
        type_members = []

        for m in ofclass.members:
            if type(m) == OFTypeMember:
                members.append(OFTypeMember(
                    name=m.name,
                    oftype=oftype.OFType(m.oftype, version),
                    value=m.value))
                type_members.append(members[-1])
            elif type(m) == OFLengthMember:
                members.append(OFLengthMember(
                    name=m.name,
                    oftype=oftype.OFType(m.oftype, version)))
            elif type(m) == OFFieldLengthMember:
                members.append(OFFieldLengthMember(
                    name=m.name,
                    oftype=oftype.OFType(m.oftype, version),
                    field_name=m.field_name))
            elif type(m) == OFPadMember:
                members.append(m)
            elif type(m) == OFDataMember:
                if utils.class_is_message(ofclass.name) and m.name == 'version':
                    # HACK move to frontend
                    members.append(OFTypeMember(
                        name=m.name,
                        oftype=oftype.OFType(m.oftype, version),
                        value=version))
                    type_members.append(members[-1])
                else:
                    members.append(OFDataMember(
                        name=m.name,
                        oftype=oftype.OFType(m.oftype, version)))

        ofclasses.append(
            PyOFClass(name=cls,
                      pyname=generate_pyname(cls),
                      members=members,
                      type_members=type_members,
                      min_length=of_g.base_length[(cls, version)],
                      is_fixed_length=(cls, version) in of_g.is_fixed_length))
    return ofclasses

def generate_init(out, name, version):
    util.render_template(out, 'init.py', version=version)

def generate_action(out, name, version):
    ofclasses = [x for x in build_ofclasses(version)
                 if utils.class_is_action(x.name)]
    util.render_template(out, 'action.py', ofclasses=ofclasses, version=version)

def generate_oxm(out, name, version):
    ofclasses = [x for x in build_ofclasses(version)
                 if utils.class_is_oxm(x.name)]
    util.render_template(out, 'oxm.py', ofclasses=ofclasses, version=version)

def generate_common(out, name, version):
    ofclasses = [x for x in build_ofclasses(version)
                 if not utils.class_is_message(x.name)
                    and not utils.class_is_action(x.name)
                    and not utils.class_is_instruction(x.name)
                    and not utils.class_is_meter_band(x.name)
                    and not utils.class_is_oxm(x.name)
                    and not utils.class_is_list(x.name)]
    util.render_template(out, 'common.py', ofclasses=ofclasses, version=version)

def generate_const(out, name, version):
    groups = {}
    for (group, idents) in of_g.identifiers_by_group.items():
        items = []
        for ident in idents:
            info = of_g.identifiers[ident]
            if version in info["values_by_version"]:
                items.append((info["ofp_name"], info["values_by_version"][version]))
        if items:
            groups[group] = items
    util.render_template(out, 'const.py', version=version, groups=groups)

def generate_instruction(out, name, version):
    ofclasses = [x for x in build_ofclasses(version)
                 if utils.class_is_instruction(x.name)]
    util.render_template(out, 'instruction.py', ofclasses=ofclasses, version=version)

def generate_message(out, name, version):
    ofclasses = [x for x in build_ofclasses(version)
                 if utils.class_is_message(x.name)]
    util.render_template(out, 'message.py', ofclasses=ofclasses, version=version)

def generate_meter_band(out, name, version):
    ofclasses = [x for x in build_ofclasses(version)
                 if utils.class_is_meter_band(x.name)]
    util.render_template(out, 'meter_band.py', ofclasses=ofclasses, version=version)

def generate_pp(out, name, version):
    util.render_template(out, 'pp.py')

def generate_util(out, name, version):
    util.render_template(out, 'util.py', version=version)
