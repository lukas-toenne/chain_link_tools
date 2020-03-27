# ##### BEGIN MIT LICENSE BLOCK #####
#
# Copyright (c) 2020 Lukas Toenne
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#
# ##### END MIT LICENSE BLOCK #####

# <pep8 compliant>

import bpy
import bmesh
from mathutils import (Matrix, Vector)
from bpy_types import Operator
from bpy.props import (
        BoolProperty,
        EnumProperty,
        FloatProperty,
        IntProperty,
        FloatVectorProperty,
        StringProperty,
        )


def edit_mode_out():
    bpy.ops.object.mode_set(mode='OBJECT')


def edit_mode_in():
    bpy.ops.object.mode_set(mode='EDIT')


class AddBoneChain(Operator):
    """Create a bone chain that follows a curve."""
    bl_idname = 'armature.add_bone_chain'
    bl_label = 'Add Bone Chain'
    bl_options = {'REGISTER', 'UNDO'}

    bone_count : IntProperty(
            name="Bone Count", default=10,
            min=1, soft_min=1,
            soft_max=100,
            description="Number of links in the chain",
            )

    bone_name : StringProperty(
            name="Bone Name", default="Chain",
            description="Base name for bones in the chain")

    # link_length : FloatProperty(
    #         name='Link Length', default=1.0,
    #         min=0, soft_min=0.001,
    #         soft_max=10.0,
    #         description='Length of one chain link',
    #         unit='LENGTH',
    #         )

    def curve_items(self, context):
        return [(ob.name, ob.name, "") for ob in bpy.data.objects if ob.type == 'CURVE']

    curve_enum : EnumProperty(
            name="Curve", items=curve_items,
            description="Curve object to bind to")

    def get_bone_name(self, i):
        return "{}.{:03d}".format(self.bone_name, i)

    def get_locator_name(self, i):
        return "{}.{:03d}.Locator".format(self.bone_name, i)

    def get_curve_object(self):
        return bpy.data.objects.get(self.curve_enum, None)

    @classmethod
    def poll(cls, context):
        active_object = context.active_object
        return active_object and active_object.type == 'ARMATURE'

    def execute(self, context):
        curve_obj = self.get_curve_object()
        if not curve_obj:
            self.report({'ERROR_INVALID_INPUT'}, "Invalid curve object {}".format(self.curve_enum))
            return {'CANCELLED'}
        curve = curve_obj.data
        if len(curve.splines) < 1:
            self.report({'ERROR_INVALID_INPUT'}, "Curve object must have at least one spline")
            return {'CANCELLED'}
        spline = curve.splines[0]
        spline_len = spline.calc_length()

        scene = context.scene
        arm_obj = context.active_object
        arm = arm_obj.data
        pose = arm_obj.pose

        # Setup edit bones
        edit_mode_in()

        space = arm_obj.matrix_world.inverted()
        C = scene.cursor.location
        # L = self.link_length
        L = spline_len / self.bone_count
        for i in range(self.bone_count):
            bone = arm.edit_bones.new(self.get_bone_name(i))
            bone.head = space @ (C + Vector((L, 0, 0)) * i)
            bone.tail = space @ (C + Vector((L, 0, 0)) * (i + 1))
            if i > 0:
                bone.parent = arm.edit_bones.get(self.get_bone_name(i - 1))

            locator = arm.edit_bones.new(self.get_locator_name(i))
            locator.head = space @ Vector((0, 0, 0))
            locator.tail = space @ Vector((L * 0.5, 0, 0))

        # Setup pose bone constraints
        edit_mode_out()

        for i in range(self.bone_count):
            bone = pose.bones.get(self.get_bone_name(i))
            locator = pose.bones.get(self.get_locator_name(i))

            locator_follow = locator.constraints.new('FOLLOW_PATH')
            locator_follow.target = curve_obj
            locator_follow.offset = i * curve.path_duration / self.bone_count
            locator_follow.use_curve_follow = True
            locator_follow.forward_axis = 'TRACK_NEGATIVE_Y'
            locator_follow.up_axis = 'UP_Z'

            bone_loc = bone.constraints.new('COPY_LOCATION')
            bone_loc.target = arm_obj
            bone_loc.subtarget = self.get_locator_name(i)

            bone_track = bone.constraints.new('TRACK_TO')
            bone_track.target = arm_obj
            bone_track.subtarget = self.get_locator_name((i + 1) % self.bone_count)
            bone_track.use_target_z = True

        return {'FINISHED'}

    def invoke(self, context, event):
        wm = context.window_manager
        return wm.invoke_props_dialog(self)


def get_armature_object(context):
    if not context.active_object:
        return None
    for mod in context.active_object.modifiers:
        if mod.type == 'ARMATURE':
            return mod.object

class CreateChainMeshArray(Operator):
    """Duplicate mesh data and assign vertex weights to follow a bone chain."""
    bl_idname = 'mesh.create_chain_mesh_array'
    bl_label = 'Create Chain Mesh Array'
    bl_options = {'REGISTER', 'UNDO'}

    def rootbone_items(self, context):
        arm_obj = get_armature_object(context)
        if arm_obj:
            return [(bone.name, bone.name, "") for bone in arm_obj.data.bones if bone.use_deform]

    rootbone_enum : EnumProperty(
            name="Root Bone", items=rootbone_items,
            description="Root bone of the chain")

    @classmethod
    def poll(cls, context):
        active_object = context.active_object
        return active_object and active_object.type == 'MESH' and active_object.mode == 'EDIT'

    def execute(self, context):
        arm_obj = get_armature_object(context)
        if not arm_obj:
            self.report({'ERROR_INVALID_CONTEXT'}, "Could not find armature modifier or object")
            return {'CANCELLED'}
        arm = arm_obj.data
        mesh_obj = context.active_object

        rootbone = arm.bones.get(self.rootbone_enum, None)
        if not rootbone:
            self.report({'ERROR_INVALID_CONTEXT'}, "Could not find root bone {}".format(self.rootbone_enum))
            return {'CANCELLED'}

        chain = [rootbone]
        while chain[-1].children:
            chain.append(chain[-1].children[0])

        for bone in chain:
            vg = mesh_obj.vertex_groups.get(bone.name, None)
            if vg:
                mesh_obj.vertex_groups.remove(vg)
            vg = mesh_obj.vertex_groups.new(name=bone.name)

        try:
            bm = bmesh.from_edit_mesh(mesh_obj.data)

            dvert_lay = bm.verts.layers.deform.verify()
            def assign_bone_verts(bone_name, verts):
                vg_index = mesh_obj.vertex_groups.get(bone_name).index
                for v in verts:
                    dvert = v[dvert_lay]
                    dvert[vg_index] = 1.0

            bone_space = mesh_obj.matrix_world.inverted() @ arm_obj.matrix_world

            geom=(
                [f for f in bm.faces if f.select]
                + [e for e in bm.edges if e.select]
                + [v for v in bm.verts if v.select]
            )

            p0 = 0.5 * (chain[0].head_local + chain[0].tail_local)
            for i, bone in enumerate(chain[1:]):
                dupli_result = bmesh.ops.duplicate(bm, geom=geom, use_select_history=False)
                dupli_verts = [v for v in dupli_result['geom'] if isinstance(v, bmesh.types.BMVert)]

                pi = 0.5 * (bone.head_local + bone.tail_local)
                geom_moved = bmesh.ops.translate(bm, vec=(pi - p0), space=bone_space, verts=dupli_verts)

                assign_bone_verts(bone.name, dupli_verts)

            # Assign original vgroup last, so that duplicating the verts does not copy the weights!
            assign_bone_verts(chain[0].name, [v for v in geom if isinstance(v, bmesh.types.BMVert)])

            bmesh.update_edit_mesh(mesh_obj.data, loop_triangles=True, destructive=True)
            bm.free()

        finally:
            pass

        return {'FINISHED'}

    def invoke(self, context, event):
        wm = context.window_manager
        return wm.invoke_props_dialog(self)


def register():
    bpy.utils.register_class(AddBoneChain)
    bpy.utils.register_class(CreateChainMeshArray)

def unregister():
    bpy.utils.unregister_class(AddBoneChain)
    bpy.utils.unregister_class(CreateChainMeshArray)
