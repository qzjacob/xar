"""宏观视图服务层(UA-P2)—— research/agents 层消费 Andy 勾稽活读数的入口,不 import api/app。

`view.theme_macro_view` 复用勾稽真相(ontology.macro_links)+ slx PIT 读数(经 andy_links,惰性
导入,无循环:andy_links 不 import research/app);`compact_theme_macro` 是 dossier/Chathy 共享
的 8k 压缩器;`macro_dossier_lines` 产出接地行 + known_ids(与论点证据 [registry:macro:*] 兼容)。
"""
from .view import compact_theme_macro, macro_dossier_lines, theme_macro_view

__all__ = ["compact_theme_macro", "macro_dossier_lines", "theme_macro_view"]
