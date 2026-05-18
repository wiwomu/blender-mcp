# BlenderMCP Island Mode

BlenderMCP connects an MCP client to Blender through a local socket server. This `island` branch keeps the core local Blender workflow while removing telemetry, phone-home behavior, remote asset-library calls, and remote model-generation calls.

## What still works

- Start a Blender addon socket server bound to loopback only.
- Connect the MCP server to Blender through `localhost`, `127.0.0.1`, or `::1`.
- Inspect the current scene and individual objects.
- Capture viewport screenshots for the active MCP/IDE/LLM runtime.
- Execute Blender Python in the local Blender runtime.
- Create geometry, materials, lights, cameras, and animations with local Blender scripting.
- Import user-approved local assets through Blender Python.

## Removed remote functionality

The following features are intentionally disabled in island mode because they can send prompts, images, scene context, asset queries, API keys, or files to third parties:

- Telemetry and usage reporting.
- Poly Haven category search, asset search, HDRI/texture/model downloads, and automatic import.
- Sketchfab search, preview, authenticated model download, and automatic import.
- Hyper3D/Rodin and Fal prompt/image-to-3D generation, polling, and generated asset download.
- Tencent Hunyuan official API prompt/image submission, polling, and generated ZIP download.
- Browser callouts from the addon UI.
- Remote URL-based asset imports.

## Private workarounds

- Use `execute_blender_code` to create procedural geometry and materials locally.
- Place already-approved `.blend`, `.obj`, `.fbx`, `.glb`, `.gltf`, image, texture, or HDRI files on the local filesystem and import them with Blender Python.
- If you use local model-generation tooling, run it outside BlenderMCP according to your policy and import only the resulting local files.
- Do not pass HTTP(S) URLs to BlenderMCP island-mode workflows.

## Compatibility

- Python: `>=3.10`.
- Blender: `>=3.0.0`.
- MCP server package dependency: `mcp[cli]>=1.3.0`.

Both the MCP server and Blender addon validate the Python/Blender versions before normal operation. The Blender addon also reports environment information through `get_environment_info`.

## Configuration

Environment variables:

- `BLENDER_HOST`: Blender socket host. Island mode accepts only `localhost`, `127.0.0.1`, or `::1`. Default: `127.0.0.1`.
- `BLENDER_PORT`: Blender socket port. Default: `9876`.

The addon binds to `127.0.0.1` by default and rejects non-loopback hosts.

## MCP tools

Active local tools:

- `get_environment_info`
- `get_scene_info`
- `get_object_info`
- `get_viewport_screenshot`
- `execute_blender_code`

Remote provider status tools return disabled messages in island mode:

- `get_polyhaven_status`
- `get_sketchfab_status`
- `get_hyper3d_status`
- `get_hunyuan3d_status`

## Privacy posture

Island mode does not include a telemetry client, persistent telemetry identifiers, analytics dependencies, or third-party asset/model integrations. The only intended network path is loopback communication between the MCP server and Blender.
