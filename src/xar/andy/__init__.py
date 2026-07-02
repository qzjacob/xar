"""XAR Andy — the conversational, tool-calling analyst.

Andy is a ChatGPT-style agent that answers over the whole XAR platform by invoking the
same plain in-process functions the dashboards/retrieval use (`andy.tools`), streaming its
reply token-by-token (`models.llm.complete_stream`), and persisting each conversation to
Postgres (`andy.sessions`). The multi-turn tool loop lives in `andy.agent`.
"""
