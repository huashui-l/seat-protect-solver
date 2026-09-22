# 配置目录

- 项目根目录`config.json`：当前5秒生产基准及列生成默认配置；
- `config_research_30s.json`：30秒研发启发式配置；
- `legacy/`：旧小样本或阶段性配置，不参与当前24组主实验；
- `tests/fixtures/config_100_small.json`：自动测试专用配置。

修改配置后至少运行`python -m unittest discover -s tests -v`。若修改评分权重、SSR语义或保护空座语义，还需要重新运行24组实验，历史U不能沿用。
