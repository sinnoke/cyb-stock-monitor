# 创业板股票实时监控程序

目标：基于富途 OpenAPI 实时监控中国股市创业板股票的每分钟 K 线，在出现异常波动时及时通知用户。

当前阶段先完成需求计划和技术方案设计，后续再进入原型开发。

## 文档

- [需求计划书](docs/requirements-plan.md)

## 初步目录

```text
cyb-stock-monitor/
  docs/                  # 产品需求、技术方案、接口说明
  src/                   # 后续源码
  tests/                 # 后续测试
```

## 本地运行准备

1. 启动富途 OpenD，确认监听 `127.0.0.1:11111`。

本机可用的 OpenD 目录包括：

```text
/Users/panxin/Projects/Futu_OpenD_10.2.6208_Mac
/Users/panxin/Projects/futu/Futu_OpenD
```

2. 安装依赖：

```bash
pip install -e .
```

3. 查看候选创业板代码。默认运行时会优先从富途基础信息中读取真实深市股票并过滤 `300/301`，这个命令只用于检查本地候选范围：

```bash
cyb-monitor --config config.example.yaml list-codes
```

4. 启动实时监控：

```bash
cyb-monitor --config config.example.yaml run
```

当前富途订阅额度为 `1000`，创业板股票约 `1408` 只。默认配置会用固定随机种子从创业板中抽取 `1000` 只进行监控，其余股票暂不监控。
