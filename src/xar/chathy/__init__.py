"""XAR Chathy — the conversational, tool-calling analyst.

Chathy is a ChatGPT-style agent that answers over the whole XAR platform by invoking the
same plain in-process functions the dashboards/retrieval use (`chathy.tools`), streaming its
reply token-by-token (`models.llm.complete_stream`), and persisting each conversation to
Postgres (`chathy.sessions`). The multi-turn tool loop lives in `chathy.agent`.
"""
