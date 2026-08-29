import unittest

from mobile_gui_vla_data_lab.web import (
    DEVICE_ACTION,
    DEVICE_ACTION_ASYNC,
    DEVICE_PREVIEW,
    DEVICE_PREVIEW_IMAGE,
    OPERATION_STATE,
    SESSION_ACTION_ASYNC,
    SESSION_PREVIEW,
    SESSION_PREVIEW_IMAGE,
    SESSION_STATE,
    UI_HTML,
)


class WebTests(unittest.TestCase):
    def test_ui_exposes_required_controls_and_server_side_geometry(self):
        for value in (
            "Click</strong> to tap",
            "drag</strong> to swipe",
            "Synthetic/test text only",
            "Back",
            "Home",
            "Wait",
            "preventive override",
            "post-error takeover",
            "viewport={x:0,y:0,width:r.width,height:r.height}",
            'draggable="false"',
            "dragstart',e=>e.preventDefault()",
            "selectstart',e=>e.preventDefault()",
            "screen.setPointerCapture(e.pointerId)",
            "screen.onpointercancel=()=>{clearGesture();schedulePreview()}",
            'name="capability" type="checkbox" value="tap"',
            'name="capability" type="checkbox" value="swipe"',
            'name="capability" type="checkbox" value="type"',
            "capabilityValues()",
            "grid-template-columns:minmax(260px,310px) minmax(380px,1fr) minmax(280px,330px)",
            'class="nav-dock"',
            "fitDeviceScreen()",
            "Before Start these controls prepare the device without recording",
            "if(busy)return",
            "updating preview and closing stable evidence",
            "mobile-gui-vla-active-session",
            "Restoring active trajectory and screenshot",
            "preview_id:displayedFrame.preview_id",
            "previewDelay(){return document.hidden?1200:60}",
            "await new Promise(resolve=>setTimeout(resolve,160))",
            "showGestureFeedback(start,end)",
            "if(value.pending_operation_id)",
            'id="workflowGuide"',
            'id="phaseRail"',
            'id="phasePrepare" class="phase-step is-current"',
            'id="phaseRecord" class="phase-step"',
            'id="phaseReview" class="phase-step"',
            "preparePhaseTitle:'1 · Prepare start state — NOT RECORDING'",
            "recordPhaseTitle:'2 · RECORDING — {count} steps'",
            "reviewPhaseTitle:'3 · REVIEW FINAL SCREEN — {count} steps'",
            "function syncControls()",
            ".setup-panel input,.setup-panel select,.setup-panel textarea,.setup-panel button",
            "[data-record-control]",
            "[data-review-control],[data-outcome]",
            'id="reviewOutcome"',
            'id="continueRecording"',
            "reviewing=true;syncControls()",
            "if(!reviewing){status(tr('reviewFirst'),true);return}",
            "if(displayedFrame&&!busy&&!down&&!reviewing)",
            "'/api/devices/'+encodeURIComponent($('device').value)+'/actions/async'",
        ):
            self.assertIn(value, UI_HTML)

        self.assertLess(UI_HTML.index('id="screen"'), UI_HTML.index('class="nav-dock"'))
        self.assertNotIn("Capability labels (comma separated)", UI_HTML)

    def test_language_switch_is_local_only_and_preserves_canonical_values(self):
        for value in (
            'id="languageToggle"',
            "mobile-gui-vla-language",
            "document.documentElement.lang=currentLanguage==='zh'?'zh-CN':'en'",
            "人工采集器 v0.1",
            "任务设置",
            "能力标签",
            "操作与标注",
            "applyLanguage(false)",
            '<option value="normal" data-i18n="normal">',
            '<option value="preventive_override" data-i18n="preventiveOverride">',
            '<option value="wrong_action" data-i18n="wrongAction">',
            "复核最终画面",
            "人工示范模式下已跳过",
        ):
            self.assertIn(value, UI_HTML)

    def test_model_intervention_is_a_conditional_locked_branch(self):
        for value in (
            'id="interventionScope"',
            "session.collection_mode==='model_with_human_intervention'",
            'id="intervention" data-model-control',
            'id="reason" data-model-control',
            'id="trigger" data-model-control',
            'id="proposal" data-model-control',
            "el.disabled=busy||!active||reviewing||!modelMode()",
            "No model is connected or executed.",
            "Retain the proposal or wrong model step",
        ):
            self.assertIn(value, UI_HTML)

    def test_collector_presets_and_task_id_generation_are_browser_local(self):
        for value in (
            'list="collectorPresets"',
            'id="saveCollector"',
            "mobile-gui-vla-collector-pseudonyms-v1",
            "['human-p1-01','human-p1-02','human-p1-03']",
            ".slice(0,8)",
            "rememberCollector(false)",
            'id="newTaskId"',
            "window.crypto.getRandomValues(random)",
            "return 'p1-'+date+'-'+time+'-'+random[0]",
            "prepareTaskId();await loadPreparation();status(result)",
        ):
            self.assertIn(value, UI_HTML)

    def test_session_state_route_does_not_capture_nested_resources(self):
        self.assertIsNotNone(SESSION_STATE.fullmatch("/api/sessions/session-1"))
        self.assertIsNone(SESSION_STATE.fullmatch("/api/sessions/session-1/frame"))
        self.assertIsNotNone(
            SESSION_PREVIEW.fullmatch("/api/sessions/session-1/preview")
        )
        self.assertIsNotNone(
            SESSION_PREVIEW_IMAGE.fullmatch(
                "/api/sessions/session-1/previews/preview-1"
            )
        )
        self.assertIsNotNone(DEVICE_PREVIEW.fullmatch("/api/devices/avd-p0/preview"))
        self.assertIsNotNone(DEVICE_ACTION.fullmatch("/api/devices/avd-p0/actions"))
        self.assertIsNotNone(
            DEVICE_ACTION_ASYNC.fullmatch("/api/devices/avd-p0/actions/async")
        )
        self.assertIsNotNone(
            SESSION_ACTION_ASYNC.fullmatch("/api/sessions/session-1/actions/async")
        )
        self.assertIsNotNone(OPERATION_STATE.fullmatch("/api/operations/op-1"))
        self.assertIsNotNone(
            DEVICE_PREVIEW_IMAGE.fullmatch(
                "/api/devices/avd-p0/previews/preview-1"
            )
        )


if __name__ == "__main__":
    unittest.main()
