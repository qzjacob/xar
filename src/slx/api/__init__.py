"""api —— FastAPI 服务层：把 point-in-time 读数、理论本体、过度宣称登记簿对外暴露。

唯一纪律（焊进每条响应）：soft 指标必须带 identification_status=unidentified 与 caveat 水印，
绝不把横截面相关包装成确定的因果结论。判定一律走 engine.point_in_time（防前视），
严禁在本层 SELECT latest。
"""
