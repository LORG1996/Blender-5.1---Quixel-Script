# ##### QUIXEL AB - MEGASCANS PLUGIN FOR BLENDER 5.1+ #####
import bpy, threading, os, time, json, socket
import queue

bl_info = {
    "name": "Megascans Plugin",
    "description": "Connects Blender 5.1+ to Quixel Bridge",
    "author": "Quixel / Fixed for 5.x",
    "version": (3, 9, 0),
    "blender": (5, 1, 0),
    "location": "File > Import",
    "category": "Import-Export"
}

MEGASCANS_DATA = queue.Queue()

class MS_Init_ImportProcess():
    def __init__(self, data):
        try:
            self.json_Array = json.loads(data.decode('utf-8'))
            for js in self.json_Array:
                self.process_asset(js)
        except Exception as e:
            print(f"Megascans Error: {e}")

    def process_asset(self, asset_data):
        self.data = asset_data
        self.assetName = self.data.get("name", "Asset").replace(" ", "_")
        self.assetID = self.data.get("id", "")
        self.materialName = f"{self.assetName}_{self.assetID}"
        
        old_objects = set(bpy.data.objects)
        self.import_geometry()
        
        # Оновлення для нових версій Blender
        bpy.context.view_layer.update()
        self.imported_objects = [obj for obj in bpy.data.objects if obj not in old_objects]
        
        self.setup_material()

    def import_geometry(self):
        mesh_list = self.data.get("meshList", [])
        for mesh in mesh_list:
            path = mesh.get("path")
            fmt = mesh.get("format", "").lower()
            if os.path.exists(path):
                if fmt == "obj":
                    bpy.ops.wm.obj_import(filepath=path)
                elif fmt == "fbx":
                    bpy.ops.import_scene.fbx(filepath=path)

    def setup_material(self):
        # Перевірка на існування матеріалу
        if self.materialName in bpy.data.materials:
            mat = bpy.data.materials[self.materialName]
        else:
            mat = bpy.data.materials.new(name=self.materialName)
            
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        nodes.clear()
        
        output = nodes.new('ShaderNodeOutputMaterial')
        output.location = (800, 0)
        
        # Основний шейдер
        bsdf = nodes.new('ShaderNodeBsdfPrincipled')
        bsdf.location = (0, 0)
        
        # Налаштування для Blender 5.1
        if hasattr(mat, "surface_render_method"):
             mat.surface_render_method = 'DITHERED'

        albedo_node = None
        ao_node = None
        normal_map_node = None
        trans_node = None

        textures = self.data.get("components", [])
        for tex in textures:
            t_type = tex.get("type")
            t_path = tex.get("path")
            if not os.path.exists(t_path): continue
            
            tex_node = nodes.new('ShaderNodeTexImage')
            tex_node.image = bpy.data.images.load(t_path)
            
            if t_type == "albedo":
                albedo_node = tex_node
                tex_node.location = (-950, 500)
            
            elif t_type == "normal":
                tex_node.image.colorspace_settings.name = 'Non-Color'
                tex_node.location = (-950, -250)
                normal_map_node = nodes.new('ShaderNodeNormalMap')
                normal_map_node.location = (-350, -250)
                links.new(normal_map_node.inputs['Color'], tex_node.outputs['Color'])
                links.new(bsdf.inputs['Normal'], normal_map_node.outputs['Normal'])
            
            elif t_type == "opacity":
                tex_node.image.colorspace_settings.name = 'Non-Color'
                tex_node.location = (-950, 150)
                links.new(bsdf.inputs['Alpha'], tex_node.outputs['Color'])

            elif t_type == "translucency":
                trans_node = tex_node
                tex_node.image.colorspace_settings.name = 'Non-Color'
                tex_node.location = (-950, -550)

            elif t_type == "ao":
                ao_node = tex_node
                tex_node.image.colorspace_settings.name = 'Non-Color'
                tex_node.location = (-950, 800)
            
            elif t_type == "roughness":
                tex_node.image.colorspace_settings.name = 'Non-Color'
                links.new(bsdf.inputs['Roughness'], tex_node.outputs['Color'])
                tex_node.location = (-650, 0)

            elif t_type == "displacement":
                tex_node.image.colorspace_settings.name = 'Non-Color'
                disp_node = nodes.new('ShaderNodeDisplacement')
                disp_node.location = (400, -400)
                disp_node.inputs['Scale'].default_value = 0.02
                links.new(disp_node.inputs['Height'], tex_node.outputs['Color'])
                links.new(output.inputs['Displacement'], disp_node.outputs['Displacement'])
                mat.displacement_method = 'BOTH'

        # Mix AO + Albedo
        final_color_socket = None
        if albedo_node and ao_node:
            mix_ao = nodes.new('ShaderNodeMix')
            mix_ao.data_type = 'RGBA'
            mix_ao.blend_type = 'MULTIPLY'
            mix_ao.inputs['Factor'].default_value = 1.0
            mix_ao.location = (-400, 450)
            links.new(mix_ao.inputs[6], albedo_node.outputs['Color'])
            links.new(mix_ao.inputs[7], ao_node.outputs['Color'])
            final_color_socket = mix_ao.outputs[2]
        elif albedo_node:
            final_color_socket = albedo_node.outputs['Color']
        
        if final_color_socket:
            links.new(bsdf.inputs['Base Color'], final_color_socket)

        # --- Translucency Mix Logic ---
        if trans_node:
            t_bsdf = nodes.new('ShaderNodeBsdfTranslucent')
            t_bsdf.location = (100, -300)
            links.new(t_bsdf.inputs['Color'], trans_node.outputs['Color'])
            if normal_map_node:
                links.new(t_bsdf.inputs['Normal'], normal_map_node.outputs['Normal'])
            
            # Mix Shader
            mix_shader = nodes.new('ShaderNodeMixShader')
            mix_shader.location = (500, 0)
            
            # Повзунок регулювання (Value Node)
            val_node = nodes.new('ShaderNodeValue')
            val_node.label = "Translucency Strength"
            val_node.outputs[0].default_value = 0.3 # Початкове значення
            val_node.location = (200, 150)
            
            links.new(mix_shader.inputs[0], val_node.outputs[0])
            links.new(mix_shader.inputs[1], bsdf.outputs['BSDF'])
            links.new(mix_shader.inputs[2], t_bsdf.outputs['BSDF'])
            links.new(output.inputs['Surface'], mix_shader.outputs['Shader'])
        else:
            links.new(output.inputs['Surface'], bsdf.outputs['BSDF'])

        # --- РОЗУМНЕ ПРИЗНАЧЕННЯ МАТЕРІАЛУ ---
        
        # 1. Спершу перевіряємо, чи прийшли нові об'єкти (це пріоритет для трави/паків)
        if self.imported_objects:
            for obj in self.imported_objects:
                if obj.type == 'MESH':
                    obj.data.materials.clear()
                    obj.data.materials.append(mat)
            print(f"Megascans: Assigned to {len(self.imported_objects)} new objects.")
            
        # 2. Якщо нових об'єктів немає (наприклад, імпортуємо лише матеріал на існуючу модель)
        else:
            selected_meshes = [obj for obj in bpy.context.selected_objects if obj.type == 'MESH']
            if selected_meshes:
                for obj in selected_meshes:
                    # Працюємо з активним слотом
                    if not obj.data.materials:
                        obj.data.materials.append(mat)
                    else:
                        idx = obj.active_material_index
                        if idx >= 0 and idx < len(obj.data.materials):
                            obj.data.materials[idx] = mat
                        else:
                            obj.data.materials[0] = mat
                print(f"Megascans: Assigned to active slot of {len(selected_meshes)} selected objects.")

# --- Решта коду без змін (Operator, start_server, poll_queue) ---
class MS_LiveLink_Operator(bpy.types.Operator):
    bl_idname = "bridge.plugin"
    bl_label = "Start Megascans Link"
    def execute(self, context):
        if not bpy.app.timers.is_registered(self.poll_queue):
            bpy.app.timers.register(self.poll_queue)
        if not any(t.name == "MS_Server" for t in threading.enumerate()):
            threading.Thread(target=self.start_server, name="MS_Server", daemon=True).start()
        return {'FINISHED'}

    def start_server(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(('localhost', 28888))
            s.listen(5)
            while True:
                conn, addr = s.accept()
                data = b""
                while True:
                    chunk = conn.recv(4096)
                    if not chunk: break
                    data += chunk
                if data: MEGASCANS_DATA.put(data)

    def poll_queue(self):
        while not MEGASCANS_DATA.empty():
            data = MEGASCANS_DATA.get()
            MS_Init_ImportProcess(data)
        return 0.5

def register(): bpy.utils.register_class(MS_LiveLink_Operator)
def unregister(): bpy.utils.unregister_class(MS_LiveLink_Operator)
if __name__ == "__main__": register()