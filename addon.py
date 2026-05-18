# Code created by Siddharth Ahuja: www.github.com/ahujasid © 2025
# Island-mode rewrite: local Blender socket functionality only; no telemetry or third-party calls.

import io
import json
import os
import platform
import socket
import sys
import threading
import time
import traceback
from contextlib import redirect_stdout

import bpy
import mathutils
from bpy.props import BoolProperty, IntProperty

bl_info = {
    "name": "Blender MCP",
    "author": "BlenderMCP",
    "version": (1, 2),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > BlenderMCP",
    "description": "Connect Blender to MCP locally without telemetry or third-party callouts",
    "category": "Interface",
}

ISLAND_MODE = True
MIN_PYTHON_VERSION = (3, 10)
ALLOWED_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
DEFAULT_HOST = "127.0.0.1"


def _format_version(version):
    return ".".join(str(part) for part in version)


def is_loopback_host(host):
    normalized = (host or "").strip().lower().strip("[]")
    return normalized in ALLOWED_LOOPBACK_HOSTS


def assert_loopback_host(host):
    if not is_loopback_host(host):
        raise ValueError(
            "Island mode only permits the BlenderMCP server to bind to localhost, 127.0.0.1, or ::1. "
            f"Rejected host: {host!r}"
        )


def validate_runtime_environment():
    """Validate Blender and Python compatibility before starting local command handling."""
    if tuple(bpy.app.version) < tuple(bl_info["blender"]):
        raise RuntimeError(
            f"BlenderMCP requires Blender >= {_format_version(bl_info['blender'])}; "
            f"found {_format_version(tuple(bpy.app.version))}"
        )
    if sys.version_info < MIN_PYTHON_VERSION:
        raise RuntimeError(
            f"BlenderMCP requires Python >= {_format_version(MIN_PYTHON_VERSION)}; "
            f"found {platform.python_version()}"
        )


def disabled_remote_feature(name):
    return {
        "enabled": False,
        "island_mode": True,
        "message": (
            f"{name} is disabled in island mode. Use local Blender Python, local files, "
            "or user-approved assets already present on this machine."
        ),
    }


class BlenderMCPServer:
    def __init__(self, host=DEFAULT_HOST, port=9876):
        assert_loopback_host(host)
        self.host = host
        self.port = port
        self.running = False
        self.socket = None
        self.server_thread = None

    def start(self):
        if self.running:
            print("Server is already running")
            return

        validate_runtime_environment()
        assert_loopback_host(self.host)
        self.running = True

        try:
            family = socket.AF_INET6 if self.host == "::1" else socket.AF_INET
            self.socket = socket.socket(family, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((self.host, self.port))
            self.socket.listen(1)

            self.server_thread = threading.Thread(target=self._server_loop, daemon=True)
            self.server_thread.start()
            print(f"BlenderMCP island server started on {self.host}:{self.port}")
        except Exception as e:
            print(f"Failed to start server: {e}")
            self.stop()

    def stop(self):
        self.running = False
        if self.socket:
            try:
                self.socket.close()
            except Exception:
                pass
            self.socket = None
        if self.server_thread:
            try:
                if self.server_thread.is_alive():
                    self.server_thread.join(timeout=1.0)
            except Exception:
                pass
            self.server_thread = None
        print("BlenderMCP island server stopped")

    def _server_loop(self):
        print("Island server thread started")
        self.socket.settimeout(1.0)
        while self.running:
            try:
                try:
                    client, address = self.socket.accept()
                    print(f"Connected to local client: {address}")
                    client_thread = threading.Thread(target=self._handle_client, args=(client,), daemon=True)
                    client_thread.start()
                except socket.timeout:
                    continue
                except Exception as e:
                    print(f"Error accepting connection: {e}")
                    time.sleep(0.5)
            except Exception as e:
                print(f"Error in server loop: {e}")
                if not self.running:
                    break
                time.sleep(0.5)
        print("Island server thread stopped")

    def _handle_client(self, client):
        client.settimeout(None)
        buffer = b""
        try:
            while self.running:
                try:
                    data = client.recv(8192)
                    if not data:
                        break
                    buffer += data
                    try:
                        command = json.loads(buffer.decode("utf-8"))
                        buffer = b""
                        response = self.execute_command(command)
                        client.sendall(json.dumps(response).encode("utf-8"))
                    except json.JSONDecodeError:
                        continue
                except Exception as e:
                    print(f"Error handling client data: {e}")
                    traceback.print_exc()
                    response = {"status": "error", "message": str(e)}
                    try:
                        client.sendall(json.dumps(response).encode("utf-8"))
                    except Exception:
                        pass
                    break
        finally:
            try:
                client.close()
            except Exception:
                pass
            print("Local client disconnected")

    def execute_command(self, command):
        try:
            cmd_type = command.get("type")
            params = command.get("params", {})

            handlers = {
                "get_environment_info": self.get_environment_info,
                "get_scene_info": self.get_scene_info,
                "get_object_info": self.get_object_info,
                "execute_code": self.execute_code,
                "get_viewport_screenshot": self.get_viewport_screenshot,
                "get_polyhaven_status": lambda: disabled_remote_feature("Poly Haven"),
                "get_sketchfab_status": lambda: disabled_remote_feature("Sketchfab"),
                "get_hyper3d_status": lambda: disabled_remote_feature("Hyper3D/Rodin"),
                "get_hunyuan3d_status": lambda: disabled_remote_feature("Hunyuan3D"),
            }

            if cmd_type not in handlers:
                return {"status": "error", "message": f"Unknown or disabled command: {cmd_type}"}

            def execute_wrapper():
                try:
                    result = handlers[cmd_type](**params)
                    return {"status": "success", "result": result}
                except Exception as e:
                    traceback.print_exc()
                    return {"status": "error", "message": str(e)}

            if threading.current_thread() is threading.main_thread():
                return execute_wrapper()

            result_container = []
            event = threading.Event()

            def run_on_main():
                result_container.append(execute_wrapper())
                event.set()
                return None

            bpy.app.timers.register(run_on_main, first_interval=0.0)
            if not event.wait(timeout=180.0):
                return {"status": "error", "message": "Command timed out"}
            return result_container[0]
        except Exception as e:
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    def get_environment_info(self):
        return {
            "island_mode": ISLAND_MODE,
            "telemetry_enabled": False,
            "third_party_callouts_enabled": False,
            "allowed_remote_endpoints": [],
            "allowed_hosts": sorted(ALLOWED_LOOPBACK_HOSTS),
            "server_host": self.host,
            "server_port": self.port,
            "addon_version": list(bl_info["version"]),
            "minimum_blender_version": list(bl_info["blender"]),
            "blender_version": list(bpy.app.version),
            "python_version": platform.python_version(),
            "minimum_python_version": list(MIN_PYTHON_VERSION),
        }

    def get_scene_info(self):
        scene = bpy.context.scene
        objects = []
        for obj in scene.objects:
            obj_info = {
                "name": obj.name,
                "type": obj.type,
                "location": [obj.location.x, obj.location.y, obj.location.z],
                "rotation": [obj.rotation_euler.x, obj.rotation_euler.y, obj.rotation_euler.z],
                "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
            }
            if obj.type == "MESH":
                obj_info["mesh"] = {
                    "vertices": len(obj.data.vertices),
                    "edges": len(obj.data.edges),
                    "polygons": len(obj.data.polygons),
                }
                obj_info["materials"] = [slot.material.name for slot in obj.material_slots if slot.material]
            objects.append(obj_info)

        return {
            "name": scene.name,
            "object_count": len(scene.objects),
            "objects": objects,
            "frame_current": scene.frame_current,
            "render_engine": scene.render.engine,
        }

    def get_object_info(self, name):
        obj = bpy.data.objects.get(name)
        if not obj:
            return {"error": f"Object not found: {name}"}

        info = {
            "name": obj.name,
            "type": obj.type,
            "location": [obj.location.x, obj.location.y, obj.location.z],
            "rotation": [obj.rotation_euler.x, obj.rotation_euler.y, obj.rotation_euler.z],
            "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
            "visible": obj.visible_get(),
        }

        if obj.type == "MESH":
            bbox = [obj.matrix_world @ mathutils.Vector(corner) for corner in obj.bound_box]
            info["world_bounding_box"] = [[point.x, point.y, point.z] for point in bbox]
            info["mesh"] = {
                "vertices": len(obj.data.vertices),
                "edges": len(obj.data.edges),
                "polygons": len(obj.data.polygons),
            }
            info["materials"] = [slot.material.name for slot in obj.material_slots if slot.material]

        return info

    def execute_code(self, code):
        stdout = io.StringIO()
        try:
            with redirect_stdout(stdout):
                exec(code, {"bpy": bpy, "mathutils": mathutils})
            return {"result": stdout.getvalue()}
        except Exception as e:
            traceback.print_exc()
            return {"error": str(e), "stdout": stdout.getvalue()}

    def get_viewport_screenshot(self, filepath, max_size=800, format="png"):
        try:
            width = bpy.context.scene.render.resolution_x
            height = bpy.context.scene.render.resolution_y
            scale = min(max_size / max(width, height), 1.0) if max_size else 1.0
            bpy.context.scene.render.resolution_x = int(width * scale)
            bpy.context.scene.render.resolution_y = int(height * scale)
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
            bpy.ops.screen.screenshot(filepath=filepath)
            bpy.context.scene.render.resolution_x = width
            bpy.context.scene.render.resolution_y = height
            return {"filepath": filepath, "format": format}
        except Exception as e:
            traceback.print_exc()
            return {"error": str(e)}


class BLENDERMCP_PT_Panel(bpy.types.Panel):
    bl_label = "BlenderMCP"
    bl_idname = "BLENDERMCP_PT_Panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BlenderMCP"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        layout.label(text="Island Mode: local only", icon="LOCKED")
        layout.label(text="No telemetry or third-party calls")
        layout.prop(scene, "blendermcp_port")

        if not scene.blendermcp_server_running:
            layout.operator("blendermcp.start_server", text="Start MCP Server")
        else:
            layout.operator("blendermcp.stop_server", text="Stop MCP Server")
            layout.label(text=f"Running on {DEFAULT_HOST}:{scene.blendermcp_port}")

        layout.separator()
        layout.label(text="Remote integrations disabled:")
        layout.label(text="Poly Haven, Sketchfab, Hyper3D, Hunyuan3D")


class BLENDERMCP_OT_StartServer(bpy.types.Operator):
    bl_idname = "blendermcp.start_server"
    bl_label = "Start BlenderMCP Server"
    bl_description = "Start the local-only BlenderMCP socket server"

    def execute(self, context):
        try:
            validate_runtime_environment()
            if not hasattr(bpy.types, "blendermcp_server") or bpy.types.blendermcp_server is None:
                bpy.types.blendermcp_server = BlenderMCPServer(port=context.scene.blendermcp_port)
            bpy.types.blendermcp_server.start()
            context.scene.blendermcp_server_running = True
            return {"FINISHED"}
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}


class BLENDERMCP_OT_StopServer(bpy.types.Operator):
    bl_idname = "blendermcp.stop_server"
    bl_label = "Stop BlenderMCP Server"
    bl_description = "Stop the BlenderMCP socket server"

    def execute(self, context):
        if hasattr(bpy.types, "blendermcp_server") and bpy.types.blendermcp_server:
            bpy.types.blendermcp_server.stop()
            bpy.types.blendermcp_server = None
        context.scene.blendermcp_server_running = False
        return {"FINISHED"}


classes = (
    BLENDERMCP_PT_Panel,
    BLENDERMCP_OT_StartServer,
    BLENDERMCP_OT_StopServer,
)


def register():
    validate_runtime_environment()
    bpy.types.Scene.blendermcp_port = IntProperty(
        name="Port",
        description="Loopback port for the BlenderMCP island server",
        default=9876,
        min=1024,
        max=65535,
    )
    bpy.types.Scene.blendermcp_server_running = BoolProperty(name="Server Running", default=False)
    bpy.types.blendermcp_server = None
    for cls in classes:
        bpy.utils.register_class(cls)
    print("BlenderMCP island addon registered")


def unregister():
    if hasattr(bpy.types, "blendermcp_server") and bpy.types.blendermcp_server:
        bpy.types.blendermcp_server.stop()
        bpy.types.blendermcp_server = None

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

    del bpy.types.Scene.blendermcp_port
    del bpy.types.Scene.blendermcp_server_running
    if hasattr(bpy.types, "blendermcp_server"):
        del bpy.types.blendermcp_server
    print("BlenderMCP island addon unregistered")


if __name__ == "__main__":
    register()
