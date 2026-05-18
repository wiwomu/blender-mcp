# BlenderMCP Island Mode Privacy Notice

The `island` branch is designed for local-only Blender control.

## No telemetry

BlenderMCP island mode does not collect analytics, usage metrics, prompts, scene data, screenshots, asset files, identifiers, or error reports. No telemetry client is included and no background telemetry worker is started.

## No third-party asset or model services

Island mode disables integrations that would contact remote asset libraries or model-generation services, including Poly Haven, Sketchfab, Hyper3D/Rodin, Fal, and Tencent Hunyuan official APIs.

## Allowed data flow

The allowed runtime data flow is limited to:

- The MCP server process running on this machine.
- The Blender addon socket server bound to loopback.
- The active IDE, LLM, or MCP client that the user intentionally connected.
- Local files and local Blender Python execution selected by the user.

## User responsibility

If you import local files, run local scripts, or connect this MCP server to an IDE/LLM client, review those tools and files according to your own security policy.
