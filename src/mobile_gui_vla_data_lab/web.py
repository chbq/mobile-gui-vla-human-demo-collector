"""Dependency-free local/LAN browser collector HTTP service."""

from __future__ import annotations

import json
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping
from urllib.parse import urlparse

from .collector import CollectionService
from .images import preview_jpeg


MAX_REQUEST_BYTES = 1_000_000
SESSION_FRAME = re.compile(r"^/api/sessions/([^/]+)/frame$")
SESSION_STATE = re.compile(r"^/api/sessions/([^/]+)$")
SESSION_PREVIEW = re.compile(r"^/api/sessions/([^/]+)/preview$")
SESSION_PREVIEW_IMAGE = re.compile(r"^/api/sessions/([^/]+)/previews/([^/]+)$")
SESSION_ACTION = re.compile(r"^/api/sessions/([^/]+)/actions$")
SESSION_ACTION_ASYNC = re.compile(r"^/api/sessions/([^/]+)/actions/async$")
SESSION_FINALIZE = re.compile(r"^/api/sessions/([^/]+)/finalize$")
DEVICE_PREVIEW = re.compile(r"^/api/devices/([^/]+)/preview$")
DEVICE_PREVIEW_IMAGE = re.compile(r"^/api/devices/([^/]+)/previews/([^/]+)$")
DEVICE_ACTION = re.compile(r"^/api/devices/([^/]+)/actions$")
DEVICE_ACTION_ASYNC = re.compile(r"^/api/devices/([^/]+)/actions/async$")
OPERATION_STATE = re.compile(r"^/api/operations/([^/]+)$")


def make_handler(service: CollectionService):
    class CollectorHandler(BaseHTTPRequestHandler):
        server_version = "MobileGUIVLADataLab/0.1"

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            try:
                if path == "/":
                    self._bytes(HTTPStatus.OK, UI_HTML.encode("utf-8"), "text/html; charset=utf-8")
                    return
                if path == "/api/devices":
                    self._json(HTTPStatus.OK, {"devices": service.list_devices()})
                    return
                match = OPERATION_STATE.fullmatch(path)
                if match:
                    self._json(HTTPStatus.OK, service.get_operation(match.group(1)))
                    return
                match = DEVICE_PREVIEW_IMAGE.fullmatch(path)
                if match:
                    rendered = preview_jpeg(
                        service.preparation_preview_png(match.group(1), match.group(2))
                    )
                    self._bytes(HTTPStatus.OK, rendered, "image/jpeg")
                    return
                match = SESSION_STATE.fullmatch(path)
                if match:
                    session = service.get_session(match.group(1))
                    self._json(HTTPStatus.OK, session.public_state())
                    return
                match = SESSION_FRAME.fullmatch(path)
                if match:
                    self._bytes(HTTPStatus.OK, service.current_png(match.group(1)), "image/png")
                    return
                match = SESSION_PREVIEW_IMAGE.fullmatch(path)
                if match:
                    rendered = preview_jpeg(
                        service.preview_png(match.group(1), match.group(2))
                    )
                    self._bytes(HTTPStatus.OK, rendered, "image/jpeg")
                    return
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            except Exception as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            try:
                payload = self._read_json()
                if path == "/api/sessions":
                    session = service.start_session(
                        device_alias=payload.get("device_alias"),
                        task=payload.get("task", {}),
                        collector_id=payload.get("collector_id"),
                        collection_mode=payload.get("collection_mode", "human_demo"),
                        app=payload.get("app"),
                        provenance=payload.get("provenance"),
                        training_eligible=payload.get("training_eligible", True),
                    )
                    self._json(HTTPStatus.CREATED, session.public_state())
                    return
                match = DEVICE_PREVIEW.fullmatch(path)
                if match:
                    self._json(
                        HTTPStatus.OK,
                        service.capture_preparation_preview(match.group(1)),
                    )
                    return
                match = DEVICE_ACTION.fullmatch(path)
                if match:
                    self._json(
                        HTTPStatus.OK,
                        service.execute_preparation(match.group(1), payload),
                    )
                    return
                match = DEVICE_ACTION_ASYNC.fullmatch(path)
                if match:
                    self._json(
                        HTTPStatus.ACCEPTED,
                        service.start_preparation_execute(match.group(1), payload),
                    )
                    return
                match = SESSION_ACTION_ASYNC.fullmatch(path)
                if match:
                    self._json(
                        HTTPStatus.ACCEPTED,
                        service.start_execute(match.group(1), payload),
                    )
                    return
                match = SESSION_ACTION.fullmatch(path)
                if match:
                    self._json(HTTPStatus.OK, service.execute(match.group(1), payload))
                    return
                match = SESSION_PREVIEW.fullmatch(path)
                if match:
                    self._json(
                        HTTPStatus.OK, service.capture_preview(match.group(1))
                    )
                    return
                match = SESSION_FINALIZE.fullmatch(path)
                if match:
                    record = service.finalize(
                        match.group(1),
                        outcome=payload.get("outcome"),
                        failure_family=payload.get("failure_family"),
                        contains_sensitive_data=payload.get(
                            "contains_sensitive_data", False
                        ),
                        redaction_status=payload.get("redaction_status", "clean"),
                        note=payload.get("note"),
                    )
                    self._json(HTTPStatus.OK, record)
                    return
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            except Exception as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

        def log_message(self, format: str, *args: Any) -> None:
            super().log_message(format, *args)

        def _read_json(self) -> dict[str, Any]:
            content_type = self.headers.get("Content-Type", "")
            if not content_type.startswith("application/json"):
                raise ValueError("Content-Type must be application/json")
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ValueError("invalid Content-Length") from exc
            if not 0 < length <= MAX_REQUEST_BYTES:
                raise ValueError("request body length is invalid")
            value = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("request JSON must be an object")
            return value

        def _json(self, status: HTTPStatus, payload: Mapping[str, Any]) -> None:
            rendered = json.dumps(dict(payload), sort_keys=True).encode("utf-8")
            self._bytes(status, rendered, "application/json")

        def _bytes(
            self, status: HTTPStatus, payload: bytes, content_type: str
        ) -> None:
            self.send_response(status.value)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' blob:")
            self.end_headers()
            self.wfile.write(payload)

    return CollectorHandler


def serve(
    service: CollectionService,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    if not 0 <= port <= 65535:
        raise ValueError("port is outside [0, 65535]")
    server = ThreadingHTTPServer((host, port), make_handler(service))
    try:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    finally:
        server.server_close()


UI_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mobile GUI-VLA Data Lab</title>
<style>
:root{font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#182333;background:#eef3f6;line-height:1.35;--navy:#22364b;--blue:#1769aa;--blue-soft:#eaf4fb;--border:#cad6de;--muted:#61717e;--danger:#a52b2b;--amber:#976313}*{box-sizing:border-box}body{margin:0;min-height:100vh}header{height:62px;padding:0 22px;background:var(--navy);color:white;display:flex;align-items:center;justify-content:space-between;box-shadow:0 1px 4px rgba(20,35,50,.2)}.brand{display:flex;flex-direction:column}.brand strong{font-size:18px}.brand span{font-size:11px;color:#bfd0dd;letter-spacing:.04em;text-transform:uppercase}.header-actions{display:flex;align-items:center;gap:8px}.header-badge{font-size:12px;padding:5px 9px;border:1px solid #628198;border-radius:999px;color:#dce9f1}.language-toggle{min-width:58px;padding:6px 10px;border-color:#7894a8;background:#314b62;color:white}.language-toggle:hover:not(:disabled){background:#3e5d76;border-color:#91aabd}
.workspace{display:grid;grid-template-columns:minmax(260px,310px) minmax(380px,1fr) minmax(280px,330px);gap:16px;padding:16px;max-width:1760px;margin:0 auto;align-items:start}.panel{background:white;border:1px solid var(--border);border-radius:12px;box-shadow:0 3px 12px rgba(35,54,75,.06);padding:15px}.side-panel{position:sticky;top:78px;max-height:calc(100vh - 94px);overflow:auto}.section-title{display:flex;align-items:center;justify-content:space-between;margin:0 0 12px}.section-title h2{font-size:15px;margin:0}.eyebrow{font-size:11px;font-weight:700;color:var(--blue);letter-spacing:.06em;text-transform:uppercase}.field{display:block;margin:0 0 11px}.field>span,.group-label{display:block;font-size:12px;font-weight:650;color:#334755;margin:0 0 5px}input,select,textarea,button{font:inherit}input,select,textarea{display:block;width:100%;border:1px solid #aebdc8;border-radius:7px;padding:8px 9px;background:#fff;color:#172635}input:disabled,select:disabled,textarea:disabled{background:#f0f3f5;color:#71808b}textarea{resize:vertical;min-height:68px}input:focus,select:focus,textarea:focus{outline:3px solid rgba(23,105,170,.14);border-color:var(--blue)}button{border:1px solid #aebdc8;border-radius:8px;background:#f7f9fa;color:#1c2d3a;padding:9px 11px;font-weight:650;cursor:pointer}button:hover:not(:disabled){background:#edf3f6;border-color:#8ea5b5}button:disabled{opacity:.55;cursor:not-allowed}.primary{width:100%;background:var(--blue);border-color:var(--blue);color:white;margin-top:2px}.primary:hover:not(:disabled){background:#12598f;border-color:#12598f}.helper,.field-helper{font-size:11px;color:var(--muted);margin:7px 0 0}.field-action{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:7px}.field-action button{min-width:72px;white-space:nowrap}.capability-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px;margin-bottom:12px}.check-chip{display:flex;align-items:center;gap:7px;border:1px solid #c5d1d9;border-radius:8px;padding:7px 8px;background:#f8fafb;font-size:12px;cursor:pointer}.check-chip:has(input:checked){border-color:#68a5cf;background:var(--blue-soft);color:#0e5588}.check-chip input{width:auto;margin:0;accent-color:var(--blue)}
.device-column{min-width:0;display:flex;flex-direction:column;align-items:center}.device-heading{width:100%;display:flex;align-items:center;justify-content:space-between;margin:2px 0 8px;color:#334755}.device-heading h1{font-size:15px;margin:0}.device-state{font-size:11px;color:#38734f;background:#e9f6ee;border:1px solid #b9dec7;border-radius:999px;padding:4px 8px}.phase-rail{width:min(100%,620px);display:grid;grid-template-columns:repeat(3,1fr);list-style:none;margin:0 0 8px;padding:0;position:relative}.phase-rail::before{content:"";position:absolute;top:15px;left:16.7%;right:16.7%;height:3px;background:#d4dde3;z-index:0}.phase-step{min-width:0;display:flex;flex-direction:column;align-items:center;text-align:center;color:#788793;position:relative;z-index:1}.phase-dot{width:32px;height:32px;display:grid;place-items:center;border-radius:50%;border:3px solid #d4dde3;background:#eef3f6;font-size:12px;font-weight:800}.phase-label{font-size:11px;font-weight:750;margin-top:4px}.phase-step.is-current{color:#0e5c91}.phase-step.is-current .phase-dot{border-color:var(--blue);background:var(--blue);color:white;box-shadow:0 0 0 4px rgba(23,105,170,.13)}.phase-step.is-complete{color:#397550}.phase-step.is-complete .phase-dot{border-color:#55a674;background:#55a674;color:white}.workflow-guide{width:min(100%,620px);display:flex;align-items:center;gap:10px;padding:9px 12px;margin:0 0 15px;border:1px solid #edcd91;background:#fff8e9;color:#6f4a0e;border-radius:10px}.workflow-guide.recording{border-color:#91c1df;background:#edf7fd;color:#164f77}.workflow-guide.reviewing{border-color:#b4a5df;background:#f4f0ff;color:#4b367b}.workflow-icon{width:26px;height:26px;flex:0 0 auto;display:grid;place-items:center;border-radius:50%;background:#d99b2b;color:white;font-weight:800}.workflow-guide.recording .workflow-icon{background:var(--blue)}.workflow-guide.reviewing .workflow-icon{background:#7055b4}.workflow-copy{display:flex;flex-direction:column;font-size:11px}.workflow-copy strong{font-size:13px}.phone-frame{width:342px;height:760px;max-width:100%;background:#101419;border:0;border-radius:18px;box-shadow:0 0 0 8px #26313a,0 12px 32px rgba(22,36,48,.18);display:flex;align-items:center;justify-content:center;position:relative;overflow:hidden}.phone-frame.busy::after{content:attr(data-busy-message);position:absolute;z-index:4;inset:auto 10px 10px 10px;padding:9px;background:rgba(8,15,21,.88);color:white;text-align:center;border-radius:7px;font-size:12px;pointer-events:none}.gesture-feedback{position:absolute;z-index:3;height:5px;border-radius:999px;background:#49b4f2;box-shadow:0 0 0 2px rgba(255,255,255,.75),0 0 10px rgba(30,150,220,.9);transform-origin:left center;opacity:0;pointer-events:none}.gesture-feedback::after{content:"";position:absolute;right:-4px;top:-5px;border-left:10px solid #49b4f2;border-top:7px solid transparent;border-bottom:7px solid transparent}.gesture-feedback.show{animation:gesturePulse .65s ease-out}@keyframes gesturePulse{0%{opacity:0;filter:brightness(1.5)}18%{opacity:1}70%{opacity:.9}100%{opacity:0}}.empty-screen{position:absolute;inset:0;display:grid;place-content:center;text-align:center;padding:26px;color:#9eb0bc;font-size:13px;pointer-events:none}.phone-frame.has-frame .empty-screen{display:none}#frame{display:block;width:100%;height:100%;object-fit:contain;touch-action:none;user-select:none;-webkit-user-select:none;-webkit-user-drag:none;cursor:crosshair}.phone-frame.reviewing #frame{cursor:not-allowed;filter:saturate(.8)}.nav-dock{display:grid;grid-template-columns:repeat(3,94px);gap:9px;margin-top:12px}.nav-key{min-height:48px;background:white;box-shadow:0 2px 7px rgba(35,54,75,.08);display:flex;align-items:center;justify-content:center;gap:7px}.nav-key .symbol{font-size:18px;line-height:1}.screen-help{max-width:620px;text-align:center;color:var(--muted);font-size:12px;margin:10px 12px 0}.screen-help strong{color:#3c5262}
.tool-section+.tool-section{border-top:1px solid #dce4e9;margin-top:15px;padding-top:15px}.tool-section h3{font-size:13px;margin:0 0 10px}.inline-action{display:grid;grid-template-columns:1fr auto;gap:7px}.inline-action button{min-width:70px}.intervention-scope{border:1px solid #d5dee5;background:#f5f8fa;border-radius:8px;padding:9px;margin-bottom:10px;font-size:11px;color:#586a77}.intervention-scope strong{display:block;color:#344956;margin-bottom:3px}.intervention-scope.model-active{border-color:#d2b576;background:#fff8e9;color:#704c12}.review-actions{display:grid;grid-template-columns:1fr;gap:7px;margin-bottom:10px}.review-actions .secondary-action{background:white}.outcome-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:7px}.outcome-grid.secondary{grid-template-columns:repeat(2,1fr);margin-top:7px}.privacy-check{display:flex;align-items:flex-start;gap:7px;font-size:12px;margin-top:10px;color:#3b4d5a}.privacy-check input{width:auto;margin:2px 0 0;accent-color:var(--danger)}.status{white-space:pre-wrap;overflow-wrap:anywhere;font:11px ui-monospace,SFMono-Regular,Consolas,monospace;background:#f3f6f8;border:1px solid #d8e1e7;border-radius:8px;padding:9px;min-height:66px;max-height:180px;overflow:auto;color:#344956}.danger{color:var(--danger);background:#fff2f2;border-color:#e4b9b9}
@media(max-width:1180px){.workspace{grid-template-columns:minmax(250px,300px) minmax(360px,1fr)}.action-panel{position:static;grid-column:1/-1;max-height:none;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 18px}.action-panel>.section-title,.action-panel>.status{grid-column:1/-1}.tool-section+.tool-section{margin-top:0;padding-top:0;border-top:0}.tool-section:nth-of-type(3){border-top:1px solid #dce4e9;padding-top:14px;grid-column:1/-1}}
@media(max-width:760px){header{height:auto;min-height:58px;padding:10px 14px}.header-badge{display:none}.workspace{grid-template-columns:1fr;padding:10px;gap:12px}.device-column{grid-row:1}.side-panel,.action-panel{position:static;max-height:none}.action-panel{display:block;grid-column:auto}.tool-section+.tool-section{border-top:1px solid #dce4e9;margin-top:15px;padding-top:15px}.phone-frame{max-height:68vh}.nav-dock{grid-template-columns:repeat(3,minmax(76px,94px))}}
</style></head>
<body>
<header><div class="brand"><strong>Mobile GUI-VLA Data Lab</strong><span data-i18n="subtitle">Human Collector v0.1</span></div><div class="header-actions"><div class="header-badge" data-i18n="workspaceBadge">Local · AVD workspace</div><button id="languageToggle" class="language-toggle" type="button" aria-label="Switch language">中文</button></div></header>
<main class="workspace">
<aside class="panel side-panel setup-panel">
<div class="section-title"><div><div class="eyebrow" data-i18n="session">Session</div><h2 data-i18n="taskSetup">Task setup</h2></div></div>
<label class="field"><span data-i18n="device">Device</span><select id="device"></select></label>
<label class="field"><span data-i18n="collectorPseudonym">Collector pseudonym</span><div class="field-action"><input id="collector" list="collectorPresets" autocomplete="off" placeholder="human-p1-01"><button id="saveCollector" type="button" data-i18n="saveAlias">Save</button></div><datalist id="collectorPresets"></datalist><div class="field-helper" data-i18n="collectorStorageHelp">Saved only in this browser; choose from the input suggestions.</div></label>
<label class="field"><span data-i18n="taskId">Task ID</span><div class="field-action"><input id="taskId" placeholder="p1-YYYYMMDD-HHMMSS-xxxx"><button id="newTaskId" type="button" data-i18n="newId">New ID</button></div></label>
<label class="field"><span data-i18n="instruction">Instruction</span><textarea id="instruction" rows="3" data-i18n-placeholder="instructionPlaceholder" placeholder="Describe the goal shown to the collector"></textarea></label>
<label class="field"><span data-i18n="taskFamily">Task family</span><input id="taskFamily" value="navigation"></label>
<div class="group-label" data-i18n="capabilityLabels">Capability labels</div>
<div class="capability-grid" role="group" aria-label="Capability labels">
<label class="check-chip"><input name="capability" type="checkbox" value="tap" checked><span data-i18n="tap">Tap</span></label>
<label class="check-chip"><input name="capability" type="checkbox" value="swipe" checked><span data-i18n="swipe">Swipe</span></label>
<label class="check-chip"><input name="capability" type="checkbox" value="type"><span data-i18n="type">Type</span></label>
<label class="check-chip"><input name="capability" type="checkbox" value="back"><span data-i18n="back">Back</span></label>
<label class="check-chip"><input name="capability" type="checkbox" value="home"><span data-i18n="home">Home</span></label>
<label class="check-chip"><input name="capability" type="checkbox" value="wait"><span data-i18n="wait">Wait</span></label>
</div>
<label class="field"><span data-i18n="dataClass">Data class</span><select id="dataClass"><option value="normal" data-i18n="normal">normal</option><option value="recovery" data-i18n="recovery">recovery</option><option value="ambiguous" data-i18n="ambiguous">ambiguous</option><option value="risk_ood" data-i18n="riskOod">risk_ood</option></select></label>
<button id="start" class="primary" data-i18n="startTrajectory">Start trajectory</button>
<p class="helper" data-i18n="taskMetadataHelp">Task metadata is captured when the trajectory starts.</p>
</aside>

<section id="deviceColumn" class="device-column">
<div class="device-heading"><h1 data-i18n="liveDevice">Live device</h1><span id="deviceState" class="device-state" data-state-key="connecting">Connecting…</span></div>
<ol id="phaseRail" class="phase-rail" aria-label="Collection progress">
<li id="phasePrepare" class="phase-step is-current" aria-current="step"><span class="phase-dot">1</span><span class="phase-label" data-i18n="phasePrepare">Prepare</span></li>
<li id="phaseRecord" class="phase-step"><span class="phase-dot">2</span><span class="phase-label" data-i18n="phaseRecord">Record</span></li>
<li id="phaseReview" class="phase-step"><span class="phase-dot">3</span><span class="phase-label" data-i18n="phaseReview">Review</span></li>
</ol>
<div id="workflowGuide" class="workflow-guide"><span id="workflowIcon" class="workflow-icon">1</span><div class="workflow-copy"><strong id="workflowTitle">Prepare start state · not recording</strong><span id="workflowDetail">Use the screen and navigation keys now. Enter the task, then press Start.</span></div></div>
<div id="screen" class="phone-frame" data-busy-message="Executing action · waiting for a stable screen…" aria-label="Interactive device screenshot"><img id="frame" draggable="false" alt="Current device screenshot"><div id="gestureFeedback" class="gesture-feedback"></div><div class="empty-screen" data-i18n-html="emptyScreen">Start a trajectory to load<br>the interactive device screen.</div></div>
<nav class="nav-dock" aria-label="Device navigation">
<button class="nav-key" data-action="back"><span class="symbol">←</span><span data-i18n="back">Back</span></button>
<button class="nav-key" data-action="home"><span class="symbol">○</span><span data-i18n="home">Home</span></button>
<button class="nav-key" data-action="wait"><span class="symbol">◷</span><span data-i18n="wait">Wait</span></button>
</nav>
<p class="screen-help" data-i18n-html="screenHelp"><strong>Click</strong> to tap · <strong>drag</strong> to swipe. Before Start these controls prepare the device without recording. During recording every action becomes a trajectory step.</p>
</section>

<aside class="panel side-panel action-panel">
<div class="section-title"><div><div class="eyebrow" data-i18n="trajectory">Trajectory</div><h2 data-i18n="actionsAnnotation">Actions & annotation</h2></div></div>
<section class="tool-section">
<h3 data-i18n="textAction">Text action</h3>
<label class="field"><span data-i18n="syntheticText">Synthetic/test text only</span><div class="inline-action"><input id="typedText" data-record-control><button id="type" data-record-control data-i18n="type">Type</button></div></label>
</section>
<section class="tool-section">
<h3 data-i18n="interventionProvenance">Intervention provenance</h3>
<div id="interventionScope" class="intervention-scope"><strong id="interventionScopeTitle">Skipped in human-demo mode</strong><span id="interventionScopeDetail">Model controls remain locked. No model is connected or executed.</span></div>
<label class="field"><span data-i18n="intervention">Intervention</span><select id="intervention" data-model-control><option value="" data-i18n="none">none</option><option value="preventive_override" data-i18n="preventiveOverride">preventive override</option><option value="post_error_takeover" data-i18n="postErrorTakeover">post-error takeover</option></select></label>
<label class="field"><span data-i18n="reason">Reason</span><select id="reason" data-model-control><option value="grounding" data-i18n="grounding">grounding</option><option value="wrong_action" data-i18n="wrongAction">wrong_action</option><option value="history_state" data-i18n="historyState">history_state</option><option value="loop" data-i18n="loop">loop</option><option value="ambiguity" data-i18n="ambiguity">ambiguity</option><option value="risk" data-i18n="risk">risk</option><option value="other" data-i18n="other">other</option></select></label>
<label class="field"><span data-i18n="triggerStep">Trigger step index (post-error)</span><input id="trigger" data-model-control type="number" min="0"></label>
<label class="field"><span data-i18n="scriptedProposal">Scripted proposal JSON (fixture only)</span><textarea id="proposal" data-model-control rows="2">{"type":"tap","x_px":1,"y_px":1}</textarea></label>
</section>
<section class="tool-section">
<h3 data-i18n="finishTrajectory">Finish trajectory</h3>
<div class="review-actions"><button id="reviewOutcome" class="primary" data-record-control data-i18n="reviewOutcome">Review final screen</button><button id="continueRecording" class="secondary-action" data-review-control data-i18n="continueRecording">Continue recording</button></div>
<label class="field"><span data-i18n="finalNote">Final note</span><input id="note" data-review-control></label>
<div class="outcome-grid"><button data-outcome="success" data-i18n="success">Success</button><button data-outcome="partial" data-i18n="partial">Partial</button><button data-outcome="failure" data-i18n="failure">Failure</button></div>
<div class="outcome-grid secondary"><button data-outcome="aborted" data-i18n="abort">Abort</button><button data-outcome="env_error" data-i18n="envError">Env Error</button></div>
<label class="privacy-check"><input id="sensitive" data-review-control type="checkbox"><span data-i18n="sensitiveQuarantine">Sensitive/private content — quarantine this trajectory</span></label>
</section>
<section class="tool-section"><h3 data-i18n="sessionStatus">Session status</h3><div id="status" class="status">Loading devices…</div></section>
</aside>
</main>
<script>
const $=id=>document.getElementById(id);let session=null,displayedFrame=null,down=null,activePointerId=null,busy=false,reviewing=false,previewTimer=null,previewGeneration=0;
const translations={
en:{subtitle:'Human Collector v0.1',workspaceBadge:'Local · AVD workspace',session:'Session',taskSetup:'Task setup',device:'Device',collectorPseudonym:'Collector pseudonym',saveAlias:'Save',collectorStorageHelp:'Saved only in this browser; choose from the input suggestions.',aliasSaved:'Saved collector pseudonym: {alias}',aliasRequired:'Enter a collector pseudonym before saving.',taskId:'Task ID',newId:'New ID',newTaskIdReady:'Generated task ID: {id}',instruction:'Instruction',instructionPlaceholder:'Describe the goal shown to the collector',taskFamily:'Task family',capabilityLabels:'Capability labels',tap:'Tap',swipe:'Swipe',type:'Type',back:'Back',home:'Home',wait:'Wait',dataClass:'Data class',normal:'normal',recovery:'recovery',ambiguous:'ambiguous',riskOod:'risk_ood',startTrajectory:'Start recording',taskMetadataHelp:'Step 1: prepare the start state with the device controls. Step 2: start recording. Step 3: review the final screen and label the outcome.',liveDevice:'Live device',connecting:'Connecting…',emptyScreen:'Loading the interactive<br>device screen…',screenHelp:'<strong>Click</strong> to tap · <strong>drag</strong> to swipe. Before Start these controls prepare the device without recording. During recording every action becomes a trajectory step.',trajectory:'Trajectory',actionsAnnotation:'Actions & annotation',textAction:'Text action',syntheticText:'Synthetic/test text only',interventionProvenance:'Intervention provenance',intervention:'Intervention',none:'none',preventiveOverride:'preventive override',postErrorTakeover:'post-error takeover',reason:'Reason',grounding:'grounding',wrongAction:'wrong_action',historyState:'history_state',loop:'loop',ambiguity:'ambiguity',risk:'risk',other:'other',triggerStep:'Trigger step index (post-error)',scriptedProposal:'Scripted proposal JSON (fixture only)',finishTrajectory:'Finish trajectory',reviewOutcome:'Review final screen',continueRecording:'← Continue recording',finalNote:'Final note',success:'Goal reached · Success',partial:'Partial',failure:'Failure',abort:'Abort',envError:'Env Error',sensitiveQuarantine:'Sensitive/private content — quarantine this trajectory',sessionStatus:'Session status',loadingDevices:'Loading devices…',busyOverlay:'Action accepted · updating preview and closing stable evidence…',previewLoadError:'The updated preview could not be loaded',previousEnded:'The previous trajectory has ended. Start a new trajectory.',restoring:'Restoring active trajectory and screenshot…',resumed:'Resumed active trajectory {id}',ready:'Ready',noDevice:'No device',unavailable:'Unavailable',live:'Live',livePreview:'Live preview: {message}',starting:'Starting recording and capturing the clean first frame…',startFirst:'Load a device preview first',executing:'Sending {action}…',preparing:'Preparing device (not recorded): {action}…',prepared:'Start state updated · this action was not recorded.',actionProgress:'Action accepted · {stage} · {seconds}s',stageQueued:'queued',stageDispatching:'sent to device',stageCapturing:'updating preview / checking stability',finalizing:'Finalizing trajectory…',noActive:'No active trajectory',phasePrepare:'Prepare',phaseRecord:'Record',phaseReview:'Review',preparePhaseTitle:'1 · Prepare start state — NOT RECORDING',preparePhaseDetail:'Use the screen and navigation keys now. Enter the task, then press Start recording.',recordPhaseTitle:'2 · RECORDING — {count} steps',recordPhaseDetail:'Perform only task actions. When finished, press Review final screen; outcome buttons remain locked until then.',reviewPhaseTitle:'3 · REVIEW FINAL SCREEN — {count} steps',reviewPhaseDetail:'Device input is frozen. Verify the visible result, then label it; or return to recording.',reviewStarted:'Review mode: device actions are frozen until you label the outcome or continue recording.',recordingContinued:'Recording resumed. Continue with task actions.',reviewFirst:'Enter final-screen review before choosing an outcome.',humanModeScopeTitle:'Skipped in human-demo mode',humanModeScopeDetail:'Model controls remain locked. No model is connected or executed.',modelModeScopeTitle:'Model intervention checkpoint',modelModeScopeDetail:'Retain the proposal or wrong model step, label the intervention, then execute the human correction.'},
zh:{subtitle:'人工采集器 v0.1',workspaceBadge:'本地 · AVD 工作台',session:'会话',taskSetup:'任务设置',device:'设备',collectorPseudonym:'采集者代号',saveAlias:'保存',collectorStorageHelp:'仅保存在当前浏览器中；可从输入建议里直接选择。',aliasSaved:'已保存采集者代号：{alias}',aliasRequired:'请先输入采集者代号再保存。',taskId:'任务 ID',newId:'换一个',newTaskIdReady:'已生成任务 ID：{id}',instruction:'任务指令',instructionPlaceholder:'描述提供给采集者的操作目标',taskFamily:'任务类别',capabilityLabels:'能力标签',tap:'点击',swipe:'滑动',type:'输入',back:'返回',home:'主页',wait:'等待',dataClass:'数据类别',normal:'普通',recovery:'恢复',ambiguous:'歧义',riskOod:'风险 / 分布外',startTrajectory:'开始记录',taskMetadataHelp:'第 1 步：准备设备起始状态；第 2 步：开始记录；第 3 步：复核最终画面并标注结果。',liveDevice:'实时设备',connecting:'连接中…',emptyScreen:'正在加载<br>可交互设备画面…',screenHelp:'<strong>单击</strong>执行点击 · <strong>拖动</strong>执行滑动。开始前的操作只准备设备、不写入轨迹；开始后每个操作都会成为轨迹步骤。',trajectory:'轨迹',actionsAnnotation:'操作与标注',textAction:'文本操作',syntheticText:'仅限合成 / 测试文本',interventionProvenance:'干预溯源',intervention:'干预',none:'无',preventiveOverride:'预防性覆盖',postErrorTakeover:'错误后接管',reason:'原因',grounding:'定位 / 落点',wrongAction:'错误操作',historyState:'历史状态',loop:'循环',ambiguity:'歧义',risk:'风险',other:'其他',triggerStep:'触发步骤索引（错误后）',scriptedProposal:'脚本提案 JSON（仅 fixture）',finishTrajectory:'结束轨迹',reviewOutcome:'复核最终画面',continueRecording:'← 返回继续采集',finalNote:'最终备注',success:'目标已达成 · 成功',partial:'部分完成',failure:'失败',abort:'中止',envError:'环境错误',sensitiveQuarantine:'包含敏感 / 隐私内容 — 隔离此轨迹',sessionStatus:'会话状态',loadingDevices:'正在加载设备…',busyOverlay:'操作已接收 · 正在更新预览并闭合稳定帧证据…',previewLoadError:'无法加载更新后的预览画面',previousEnded:'上一条轨迹已结束，请开始新轨迹。',restoring:'正在恢复活动轨迹和截图…',resumed:'已恢复活动轨迹 {id}',ready:'就绪',noDevice:'无设备',unavailable:'不可用',live:'实时',livePreview:'实时预览：{message}',starting:'正在开始记录并捕获干净首帧…',startFirst:'请先加载设备画面',executing:'正在发送{action}…',preparing:'正在准备设备（不记录）：{action}…',prepared:'起始状态已更新 · 此操作未写入轨迹。',actionProgress:'操作已接收 · {stage} · {seconds} 秒',stageQueued:'排队中',stageDispatching:'已发送到设备',stageCapturing:'正在更新预览 / 检查稳定性',finalizing:'正在结束轨迹…',noActive:'没有活动轨迹',phasePrepare:'准备',phaseRecord:'采集',phaseReview:'复核',preparePhaseTitle:'1 · 准备起始状态 — 当前不记录',preparePhaseDetail:'现在可操作屏幕和导航键。填写任务后，再点“开始记录”。',recordPhaseTitle:'2 · 正在记录 — 已有 {count} 步',recordPhaseDetail:'只执行任务动作；完成后点击“复核最终画面”，结果按钮此前保持锁定。',reviewPhaseTitle:'3 · 复核最终画面 — 共 {count} 步',reviewPhaseDetail:'设备操作已冻结。核对画面后标注结果，或返回继续采集。',reviewStarted:'已进入复核：设备操作被冻结，请标注结果或返回继续采集。',recordingContinued:'已返回采集阶段，请继续执行任务动作。',reviewFirst:'请先进入最终画面复核，再选择结果。',humanModeScopeTitle:'人工示范模式下已跳过',humanModeScopeDetail:'模型介入控件保持锁定；当前未连接、也不会执行模型。',modelModeScopeTitle:'模型介入检查点',modelModeScopeDetail:'先保留模型提案或错误步骤，再标注介入信息，最后执行人工纠正。'}
};
let currentLanguage=localStorage.getItem('mobile-gui-vla-language')==='zh'?'zh':'en';
function tr(key,values={}){let text=(translations[currentLanguage]&&translations[currentLanguage][key])||translations.en[key]||key;for(const [name,value] of Object.entries(values))text=text.replaceAll('{'+name+'}',value);return text}
function applyLanguage(persist=true){document.documentElement.lang=currentLanguage==='zh'?'zh-CN':'en';for(const el of document.querySelectorAll('[data-i18n]'))el.textContent=tr(el.dataset.i18n);for(const el of document.querySelectorAll('[data-i18n-html]'))el.innerHTML=tr(el.dataset.i18nHtml);for(const el of document.querySelectorAll('[data-i18n-placeholder]'))el.placeholder=tr(el.dataset.i18nPlaceholder);$('screen').dataset.busyMessage=tr('busyOverlay');$('languageToggle').textContent=currentLanguage==='en'?'中文':'EN';$('languageToggle').setAttribute('aria-label',currentLanguage==='en'?'切换到中文':'Switch to English');updateWorkflow();if(persist)localStorage.setItem('mobile-gui-vla-language',currentLanguage)}
function setDeviceState(key,suffix=''){$('deviceState').dataset.stateKey=key;$('deviceState').dataset.stateSuffix=suffix;$('deviceState').textContent=tr(key)+suffix}
const defaultCollectorAliases=['human-p1-01','human-p1-02','human-p1-03'],collectorStorageKey='mobile-gui-vla-collector-pseudonyms-v1';
function savedCollectorAliases(){try{const values=JSON.parse(localStorage.getItem(collectorStorageKey)||'[]');if(!Array.isArray(values))return [];return [...new Set(values.filter(value=>typeof value==='string').map(value=>value.trim()).filter(Boolean))].slice(0,8)}catch{return []}}
function renderCollectorAliases(){const aliases=[...new Set([...savedCollectorAliases(),...defaultCollectorAliases])],list=$('collectorPresets');list.innerHTML='';for(const alias of aliases){const option=document.createElement('option');option.value=alias;list.appendChild(option)}if(!$('collector').value.trim())$('collector').value=aliases[0]||''}
function rememberCollector(announce=true){const alias=$('collector').value.trim();if(!alias){if(announce)status(tr('aliasRequired'),true);return false}const values=[alias,...savedCollectorAliases().filter(value=>value!==alias)].slice(0,8);localStorage.setItem(collectorStorageKey,JSON.stringify(values));renderCollectorAliases();if(announce)status(tr('aliasSaved',{alias}));return true}
function generatedTaskId(){const now=new Date(),pad=value=>String(value).padStart(2,'0'),date=now.getFullYear()+pad(now.getMonth()+1)+pad(now.getDate()),time=pad(now.getHours())+pad(now.getMinutes())+pad(now.getSeconds()),random=new Uint16Array(1);window.crypto.getRandomValues(random);return 'p1-'+date+'-'+time+'-'+random[0].toString(16).padStart(4,'0')}
function prepareTaskId(announce=false){$('taskId').value=generatedTaskId();if(announce)status(tr('newTaskIdReady',{id:$('taskId').value}))}
async function api(path,method='GET',body){const r=await fetch(path,{method,headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});const data=await r.json();if(!r.ok)throw new Error(data.error||r.statusText);return data}
function operationStage(value){if(value==='DISPATCHING')return tr('stageDispatching');if(value==='CAPTURING_STABLE_FRAME')return tr('stageCapturing');return tr('stageQueued')}
async function waitForOperation(operationId){const startedAt=performance.now();let shownPreviewId=null;for(;;){const operation=await api('/api/operations/'+encodeURIComponent(operationId));if(operation.preview&&operation.preview.preview_id!==shownPreviewId){await loadPreview(operation.preview);shownPreviewId=operation.preview.preview_id}if(operation.state==='COMPLETE')return operation.result;if(operation.state==='ERROR')throw new Error(operation.error||'action operation failed');status(tr('actionProgress',{stage:operationStage(operation.stage),seconds:((performance.now()-startedAt)/1000).toFixed(1)}));await new Promise(resolve=>setTimeout(resolve,160))}}
function status(value,bad=false){$('status').textContent=typeof value==='string'?value:JSON.stringify(value,null,2);$('status').className='status'+(bad?' danger':'')}
function frameToken(){return {preview_id:displayedFrame.preview_id,frame_id:displayedFrame.frame_id,frame_sha256:displayedFrame.sha256}}
function modelMode(){return Boolean(session&&session.collection_mode==='model_with_human_intervention')}
function updateInterventionScope(){const active=modelMode(),scope=$('interventionScope');scope.classList.toggle('model-active',active);$('interventionScopeTitle').textContent=tr(active?'modelModeScopeTitle':'humanModeScopeTitle');$('interventionScopeDetail').textContent=tr(active?'modelModeScopeDetail':'humanModeScopeDetail')}
function updateWorkflow(){const active=Boolean(session),count=active?(session.step_count||0):0,stage=!active?'prepare':reviewing?'review':'record',order=['prepare','record','review'];for(const [index,name] of order.entries()){const el=$('phase'+name[0].toUpperCase()+name.slice(1)),position=order.indexOf(stage);el.classList.toggle('is-current',name===stage);el.classList.toggle('is-complete',index<position);if(name===stage)el.setAttribute('aria-current','step');else el.removeAttribute('aria-current')}$('workflowGuide').classList.toggle('recording',stage==='record');$('workflowGuide').classList.toggle('reviewing',stage==='review');$('workflowIcon').textContent=String(order.indexOf(stage)+1);const titleKey=stage==='prepare'?'preparePhaseTitle':stage==='record'?'recordPhaseTitle':'reviewPhaseTitle',detailKey=stage==='prepare'?'preparePhaseDetail':stage==='record'?'recordPhaseDetail':'reviewPhaseDetail';$('workflowTitle').textContent=tr(titleKey,{count});$('workflowDetail').textContent=tr(detailKey);$('screen').classList.toggle('reviewing',stage==='review');updateInterventionScope()}
function syncControls(){const active=Boolean(session);for(const el of document.querySelectorAll('.setup-panel input,.setup-panel select,.setup-panel textarea,.setup-panel button'))el.disabled=busy||active;for(const el of document.querySelectorAll('.action-panel input,.action-panel select,.action-panel textarea,.action-panel button'))el.disabled=true;for(const el of document.querySelectorAll('[data-record-control]'))el.disabled=busy||!active||reviewing;for(const el of document.querySelectorAll('[data-model-control]'))el.disabled=busy||!active||reviewing||!modelMode();for(const el of document.querySelectorAll('[data-review-control],[data-outcome]'))el.disabled=busy||!active||!reviewing;for(const el of document.querySelectorAll('.nav-dock button'))el.disabled=busy||!displayedFrame||(active&&reviewing);$('languageToggle').disabled=false;updateWorkflow()}
function setBusy(value,message){busy=value;$('screen').classList.toggle('busy',value);$('screen').setAttribute('aria-busy',String(value));syncControls();if(message)status(message)}
function stopPreview(){previewGeneration++;if(previewTimer!==null)clearTimeout(previewTimer);previewTimer=null}
function previewDelay(){return document.hidden?1200:60}
function schedulePreview(){if(previewTimer!==null)clearTimeout(previewTimer);if(displayedFrame&&!busy&&!down&&!reviewing)previewTimer=setTimeout(refreshPreview,previewDelay())}
function forgetSession(){stopPreview();session=null;displayedFrame=null;reviewing=false;localStorage.removeItem('mobile-gui-vla-active-session');syncControls()}
function fitDeviceScreen(){if(!displayedFrame)return;const column=$('deviceColumn'),shell=$('screen'),ratio=displayedFrame.width_px/displayedFrame.height_px,maxHeight=Math.min(window.innerHeight*.68,780),maxWidth=Math.max(240,column.clientWidth-12),height=Math.min(maxHeight,maxWidth/ratio);shell.style.width=Math.round(height*ratio)+'px';shell.style.height=Math.round(height)+'px'}
function loadPreview(value){return new Promise((resolve,reject)=>{const frame=$('frame');frame.onload=()=>{displayedFrame=value;$('screen').classList.add('has-frame');fitDeviceScreen();setDeviceState('live',' · '+value.width_px+'×'+value.height_px);syncControls();resolve()};frame.onerror=()=>reject(new Error(tr('previewLoadError')));frame.src=value.url+'?v='+encodeURIComponent(value.sha256)+'&t='+Date.now()})}
async function showSession(value){session=value;reviewing=false;if(value.task&&value.task.task_id)$('taskId').value=value.task.task_id;localStorage.setItem('mobile-gui-vla-active-session',value.session_id);syncControls();await loadPreview(value.frame)}
async function loadPreparation(){const alias=$('device').value;if(!alias)return;const result=await api('/api/devices/'+encodeURIComponent(alias)+'/preview','POST',{});await loadPreview(result.preview);syncControls()}
async function refreshPreview(){previewTimer=null;if(!displayedFrame||busy||down||reviewing){schedulePreview();return}const generation=++previewGeneration;try{const path=session?'/api/sessions/'+session.session_id+'/preview':'/api/devices/'+encodeURIComponent($('device').value)+'/preview';const result=await api(path,'POST',{});if(generation!==previewGeneration||busy||down||reviewing)return;await loadPreview(result.preview)}catch(e){if(generation===previewGeneration){if(session&&/not active|unknown session/i.test(e.message)){forgetSession();status(tr('previousEnded'),true);await loadPreparation()}else status(tr('livePreview',{message:e.message}),true)}}finally{schedulePreview()}}
async function resumeSession(){const saved=localStorage.getItem('mobile-gui-vla-active-session');if(!saved)return false;let restored=false;try{const value=await api('/api/sessions/'+encodeURIComponent(saved));if(value.state!=='ACTIVE'){forgetSession();return false}setBusy(true,tr('restoring'));await showSession(value);restored=true;if(value.pending_operation_id){const result=await waitForOperation(value.pending_operation_id);await showSession(result.session)}status(tr('resumed',{id:value.trajectory_id}));return true}catch(e){if(restored){status(e.message,true);return true}forgetSession();return false}finally{setBusy(false);schedulePreview()}}
async function loadDevices(){try{const d=await api('/api/devices');$('device').innerHTML='';for(const item of d.devices){const o=document.createElement('option');o.value=item.device_alias;o.textContent=item.device_alias;$('device').appendChild(o)}setDeviceState(d.devices.length?'ready':'noDevice');if(!await resumeSession()){await loadPreparation();status(d);schedulePreview()}}catch(e){setDeviceState('unavailable');status(e.message,true)}finally{syncControls()}}
function capabilityValues(){return [...document.querySelectorAll('input[name="capability"]:checked')].map(input=>input.value)}
$('saveCollector').onclick=()=>rememberCollector();
$('newTaskId').onclick=()=>prepareTaskId(true);
$('device').onchange=async()=>{if(session||busy)return;stopPreview();setBusy(true,tr('connecting'));try{displayedFrame=null;await loadPreparation();status(tr('prepared'))}catch(e){status(e.message,true)}finally{setBusy(false);schedulePreview()}};
$('start').onclick=async()=>{if(busy)return;stopPreview();setBusy(true,tr('starting'));try{const collectorId=$('collector').value.trim(),task={task_id:$('taskId').value.trim(),instruction:$('instruction').value,task_family:$('taskFamily').value,capability_labels:capabilityValues(),data_class:$('dataClass').value};const value=await api('/api/sessions','POST',{device_alias:$('device').value,collector_id:collectorId,task});rememberCollector(false);await showSession(value);status(value)}catch(e){status(e.message,true)}finally{setBusy(false);schedulePreview()}};
function intervention(){const kind=$('intervention').value;if(!kind)return {};const value={intervention:{kind,reason:$('reason').value,trigger_step_index:$('trigger').value===''?null:Number($('trigger').value)}};if(kind==='preventive_override')value.model_proposal={source:'scripted_fixture',structured_action:JSON.parse($('proposal').value),executed:false};return value}
async function action(value){if(!displayedFrame)throw new Error(tr('startFirst'));if(reviewing)throw new Error(tr('reviewStarted'));if(busy)return;const recording=Boolean(session);stopPreview();setBusy(true,tr(recording?'executing':'preparing',{action:tr(value.type)}));try{const path=recording?'/api/sessions/'+session.session_id+'/actions/async':'/api/devices/'+encodeURIComponent($('device').value)+'/actions/async',started=await api(path,'POST',{...frameToken(),...value,...(recording?intervention():{})}),result=await waitForOperation(started.operation_id);if(recording){await showSession(result.session);status(result.session)}else{await loadPreview(result.preview);status(tr('prepared'))}}finally{setBusy(false);schedulePreview()}}
$('type').onclick=()=>action({type:'type',text:$('typedText').value}).catch(e=>status(e.message,true));
for(const b of document.querySelectorAll('[data-action]'))b.onclick=()=>action({type:b.dataset.action,duration_ms:b.dataset.action==='wait'?500:undefined}).catch(e=>status(e.message,true));
const screen=$('frame');
function clearGesture(){down=null;activePointerId=null}
function showGestureFeedback(start,end){const dx=end.x-start.x,dy=end.y-start.y,length=Math.hypot(dx,dy);if(length<5)return;const feedback=$('gestureFeedback');feedback.classList.remove('show');feedback.style.left=start.x+'px';feedback.style.top=start.y+'px';feedback.style.width=length+'px';feedback.style.transform='rotate('+Math.atan2(dy,dx)+'rad)';void feedback.offsetWidth;feedback.classList.add('show')}
screen.addEventListener('dragstart',e=>e.preventDefault());
screen.addEventListener('selectstart',e=>e.preventDefault());
screen.onpointerdown=e=>{if(!displayedFrame){status(tr('startFirst'),true);return}if(reviewing){status(tr('reviewStarted'));return}if(busy)return;if(e.pointerType==='mouse'&&e.button!==0)return;stopPreview();e.preventDefault();const r=screen.getBoundingClientRect();down={x:e.clientX-r.left,y:e.clientY-r.top};activePointerId=e.pointerId;screen.setPointerCapture(e.pointerId)};
screen.onpointermove=e=>{if(down&&e.pointerId===activePointerId)e.preventDefault()};
screen.onpointercancel=()=>{clearGesture();schedulePreview()};
screen.onpointerup=e=>{if(!down||e.pointerId!==activePointerId)return;e.preventDefault();const start=down,r=screen.getBoundingClientRect(),end={x:e.clientX-r.left,y:e.clientY-r.top},viewport={x:0,y:0,width:r.width,height:r.height};const distance=Math.hypot(end.x-start.x,end.y-start.y);const value=distance<5?{type:'tap',x:end.x,y:end.y,viewport}:{type:'swipe',x0:start.x,y0:start.y,x1:end.x,y1:end.y,duration_ms:300,viewport};showGestureFeedback(start,end);clearGesture();action(value).catch(err=>status(err.message,true))};
$('reviewOutcome').onclick=()=>{if(!session||busy)return;stopPreview();reviewing=true;syncControls();status(tr('reviewStarted'))};
$('continueRecording').onclick=()=>{if(!session||busy)return;reviewing=false;syncControls();status(tr('recordingContinued'));schedulePreview()};
$('intervention').onchange=()=>{updateInterventionScope()};
for(const b of document.querySelectorAll('[data-outcome]'))b.onclick=async()=>{if(busy)return;if(!reviewing){status(tr('reviewFirst'),true);return}stopPreview();setBusy(true,tr('finalizing'));try{if(!session)throw new Error(tr('noActive'));const sensitive=$('sensitive').checked;const result=await api('/api/sessions/'+session.session_id+'/finalize','POST',{outcome:b.dataset.outcome,contains_sensitive_data:sensitive,redaction_status:sensitive?'quarantine':'clean',note:$('note').value||null});forgetSession();prepareTaskId();await loadPreparation();status(result)}catch(e){status(e.message,true)}finally{setBusy(false);schedulePreview()}};
$('languageToggle').onclick=()=>{currentLanguage=currentLanguage==='en'?'zh':'en';applyLanguage();setDeviceState($('deviceState').dataset.stateKey,$('deviceState').dataset.stateSuffix||'')};
applyLanguage(false);renderCollectorAliases();prepareTaskId();setDeviceState('connecting');$('status').textContent=tr('loadingDevices');syncControls();window.addEventListener('resize',fitDeviceScreen);document.addEventListener('visibilitychange',()=>{stopPreview();schedulePreview()});loadDevices();
</script></body></html>"""
