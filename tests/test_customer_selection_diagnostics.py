"""Resource selection and safe diagnostics; no real keys, cloud or model calls."""
import json
import unittest
from unittest.mock import MagicMock, patch

import test_customer_beta as auth_fixtures
import test_customer_ui_refinements as ui_fixtures
from test_customer_beta import StopPage
from pilot import store
from pilot.selection_labels import resource_labels
from services.failure_details import describe_failure
from services.observability import model_call_summary


def string_selector(original):
    """Reproduce Streamlit's formatted-label serialization, including collisions."""
    def select(label,options,**kwargs):
        options=list(options)
        selected=original(label,options,**kwargs)
        formatter=kwargs.get("format_func",str)
        mapping={formatter(value):value for value in options}
        return mapping[formatter(selected)]
    return select


class WorkspaceSelectionTests(unittest.TestCase):
    setUp=auth_fixtures.CustomerAuthTests.setUp

    def duplicate_workspaces(self):
        self.auth.require_access()
        self.memberships.return_value.append({"id":"b"*32,"name":self.memberships.return_value[0]["name"]})

    def test_opening_duplicate_named_workspace_selector_keeps_original_workspace_and_draft(self):
        self.duplicate_workspaces()
        self.st.session_state.customer_workspace="a"*32
        self.st.session_state["draft_customer_claim"]="Keep my draft"
        with patch.object(self.st,"selectbox",side_effect=string_selector(self.st.selectbox)):
            for _ in range(3):
                self.assertEqual(self.auth.require_access().workspace,"a"*32)
        self.assertEqual(self.st.session_state["draft_customer_claim"],"Keep my draft")
        self.assertIn("Workspace:",self.st.rendered_text("caption"))
        self.assertIn("aaaaaaaa",self.st.rendered_text("caption"))

    def test_widget_cleanup_preserves_nondefault_durable_workspace(self):
        self.duplicate_workspaces()
        self.st.session_state.customer_workspace="b"*32
        self.auth.require_access()
        self.st.session_state.pop("customer_workspace_selector",None)
        self.assertEqual(self.auth.require_access().workspace,"b"*32)

    def test_intentional_selection_callback_updates_workspace_before_rerun(self):
        self.duplicate_workspaces()
        self.auth.require_access()
        self.st.session_state.customer_workspace_selector="b"*32
        self.auth._remember_workspace()
        self.assertEqual(self.auth.require_access().workspace,"b"*32)

    def test_selector_cannot_select_an_ungranted_workspace(self):
        self.auth.require_access()
        self.st.session_state.customer_workspace_selector="c"*32
        self.auth._remember_workspace()
        self.assertEqual(self.auth.require_access().workspace,"a"*32)


class ResourceLabelTests(unittest.TestCase):
    def test_same_names_and_short_reference_collisions_remain_distinct(self):
        entries=[{"id":"12345678"+char*24,"name":"Report.pdf"} for char in "ab"]
        labels=resource_labels(entries)
        self.assertEqual(len(set(labels.values())),2)
        self.assertTrue(all(label.startswith("Report.pdf") for label in labels.values()))
        self.assertEqual(labels,resource_labels(list(reversed(entries))))


class CustomerSelectionDetailsTests(unittest.TestCase):
    setUp=ui_fixtures.CustomerInterfaceTests.setUp
    job=ui_fixtures.CustomerInterfaceTests.job

    def test_duplicate_pdf_names_preserve_active_and_additional_document_ids(self):
        docs=[{"id":char*32,"name":"Report.pdf","status":"ready"} for char in "cd"]
        def additional(label,options,**kwargs):
            self.assertNotEqual(kwargs["format_func"](docs[0]["id"]),kwargs["format_func"](docs[1]["id"]))
            return list(options)
        with patch.object(store,"documents",return_value=docs),patch.object(store,"jobs",return_value=[]), \
             patch.object(self.st,"selectbox",side_effect=string_selector(self.st.selectbox)), \
             patch.object(self.st,"multiselect",side_effect=additional),patch.object(self.ui,"document_context"), \
             patch.object(self.ui,"review_composer") as compose:
            self.ui.reviews_page(self.access,"selection_")
        self.assertEqual(compose.call_args.args[2],["c"*32,"d"*32])

    def test_citations_on_same_page_in_same_named_pdfs_have_distinct_labels(self):
        docs=[{"id":char*32,"name":"Report.pdf","sha":char} for char in "cd"]
        evidence=[{"document_id":doc["id"],"page":1,"source_evidence_id":"same-id"} for doc in docs]
        labels=[]
        def selector(label,options,**kwargs):
            labels.extend(kwargs["format_func"](value) for value in options)
            raise StopPage()
        with patch.object(store,"documents",return_value=docs),patch.object(self.ui,"_valid_citations",return_value=evidence), \
             patch.object(self.st,"selectbox",side_effect=selector),self.assertRaises(StopPage):
            self.ui.viewer(self.access,{"evidence":evidence},"viewer_")
        self.assertEqual(len(labels),2); self.assertEqual(len(set(labels)),2)

    def test_request_details_show_actual_limit_finish_and_counts_without_private_fields(self):
        self.saved["models"]["max_output_tokens"]=8192
        call={"status":"error","task":"text","requested_model":"vendor/request-model","finish_reason":"length",
              "output_token_limit":8192,"usage":{"prompt_tokens":7,"completion_tokens":8192,"total_tokens":8199,
              "completion_tokens_details":{"reasoning_tokens":8000,"private":"PRIVATE_THOUGHT"}},
              "api_key":"PRIVATE_KEY","error_message":"PRIVATE_BODY","prompt":"PRIVATE_CLAIM"}
        self.ui.request_details(self.job(result=json.dumps({"model_calls":[call]})))
        rows=next(args[0] for name,args,_ in self.st.events if name=="dataframe")
        row=rows[0]
        self.assertEqual((row["output_token_limit"],row["finish_reason"]),(8192,"length"))
        self.assertEqual((row["input_tokens"],row["completion_tokens"],row["reasoning_tokens"],row["total_tokens"]),(7,8192,8000,8199))
        self.assertIn("8,192",self.st.rendered_text())
        self.assertNotIn("PRIVATE",self.st.rendered_text())

    def test_unreported_historical_usage_is_not_displayed_as_zero(self):
        self.ui.request_details(self.job(result=json.dumps({"model_calls":[{"status":"error"}]})))
        rows=next(args[0] for name,args,_ in self.st.events if name=="dataframe")
        for field in ("input_tokens","completion_tokens","reasoning_tokens","total_tokens","output_token_limit"):
            self.assertNotIn(field,rows[0])
        self.assertIn("missing values were not reported",self.st.rendered_text())

    def test_old_saved_claim_failure_gets_correct_advice_without_changing_saved_record(self):
        result={"failure":{"code":"output_truncated","title":"Old title","message":"Old message", "action":"Use the vision budget"},
                "model_calls":[{"status":"error","task":"text","finish_reason":"length","output_token_limit":8192}]}
        job=self.job(result=json.dumps(result)); original=job["result"]
        self.ui.show_failure(self.access,job)
        advice=self.st.rendered_text("info")
        self.assertIn("new review",advice); self.assertNotIn("vision",advice)
        self.assertIn("Finish reason: length",self.st.rendered_text())
        self.assertEqual(job["result"],original)


class SafeTokenDiagnosticsTests(unittest.TestCase):
    def test_nested_reasoning_count_is_retained_without_reasoning_content_and_is_idempotent(self):
        raw={"status":"error","finish_reason":"length","usage":{"prompt_tokens":0,"completion_tokens":8,
             "completion_tokens_details":{"reasoning_tokens":7,"reasoning":"PRIVATE_THOUGHT"}},"reasoning":"PRIVATE_THOUGHT"}
        summary=model_call_summary(raw)
        self.assertEqual(summary["usage"],{"prompt_tokens":0,"completion_tokens":8,"reasoning_tokens":7})
        self.assertNotIn("PRIVATE",json.dumps(summary))
        self.assertEqual(model_call_summary(summary)["usage"],summary["usage"])

    def test_invalid_numeric_usage_and_nonallowlisted_finish_reasons_are_omitted(self):
        result=model_call_summary({"finish_reason":"PRIVATE_BODY","usage":{"prompt_tokens":True,
            "completion_tokens":float("nan"),"total_tokens":-1,"cost":float("inf"),
            "completion_tokens_details":{"reasoning_tokens":"PRIVATE_BODY"}}})
        self.assertEqual(result["usage"],{}); self.assertIsNone(result["finish_reason"])
        self.assertNotIn("PRIVATE",json.dumps(result))

    def test_claim_and_vision_failures_receive_different_output_recovery_advice(self):
        for task,phrase in (("text","new review"),("vision","new processing")):
            with self.subTest(task=task):
                result=describe_failure(kind="output_truncated",metadata={"task":task,"finish_reason":"length"})
                self.assertIn(phrase,result["action"])
                self.assertEqual(result["finish_reason"],"length")

    def test_text_context_and_capability_errors_do_not_request_a_vision_model(self):
        for code in ("context_limit","model_incompatible"):
            with self.subTest(code=code):
                result=describe_failure(kind=code,metadata={"task":"text"})
                self.assertIn("reasoning model",result["action"])
                self.assertNotIn("vision",result["action"])
                self.assertNotIn("image input",result["message"])


if __name__=="__main__": unittest.main()
