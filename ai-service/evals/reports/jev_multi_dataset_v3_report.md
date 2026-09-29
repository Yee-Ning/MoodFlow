# Jev Multi Router V3 dataset freeze

依据 `router_capability_contract.json` v1.0 在任何 V3 预测与阈值搜索前标注。新增请求由本轮人工撰写为真实使用风格，**不是生产日志或独立盲审**；标签没有取自 Router 输出。Gold V2 历史 Multi 11 条仅作为 dev seed，不进入 blind。

| Split | 总数 | 真 Multi | 单意图 guard | 部分 unsupported / 实体模糊 |
| --- | ---: | ---: | ---: | ---: |
| multi_dev_v3 | 41 | 33 | 5 | 3 |
| multi_blind_test_v3 | 24 | 15 | 5 | 4 |
| single_blind_v3 | 20 | 0 | 0 | 0 |

新增 Multi 覆盖六种双路组合与四种三路组合；包含否定动作、条件句、多段自然语言、同义表达、顺序依赖、部分不支持任务和实体模糊。具体请求、逐条能力依据与原 Gold V2 case_id 在 JSONL 中。

`gold_routes` 只记录当前子图可承接的独立意图。`unroutable_part` 单独保留未支持或需澄清部分；这类样本的 route-set Exact Match 不等于整句请求已被完成。顺序依赖标记仅供切片分析，Jev 实验只比较 route set，不声称可以替代 Hybrid 任务拆分与依赖图。

## 支持度与冻结哈希

- multi_dev_v3: SHA256 `4d5eba1b34142593fde46997e355fdea62885f43b48de3988d7f81129174eb0c`；route support current_song=20, tool_calling=22, general_chat=23, worldline=13
- multi_blind_test_v3: SHA256 `137e9e3c479b8f1d3a309fbf1e03142c79b55dc4933d34f55bcb600c78dedc94`；route support current_song=10, tool_calling=12, general_chat=11, worldline=9
- single_blind_v3: SHA256 `8ca3cf3aca26530a3b8bfdcc9b83b0828bede2ba5b7916eb6c6f5caaeaf0118e`；route support current_song=5, tool_calling=5, general_chat=5, worldline=5

检查包括原 Gold V2 与 Contract 正反例、边界例和模糊例对 Single blind 的精确去重、所有 V3 query 的精确去重，以及 dev/blind 最大规范化字符串相似度 <0.72。语义层面的完全独立性无法由字面检查保证。

## 评测边界

所有当前歌曲请求假定有有效 SongContext；路由评测不调用下游服务。模糊实体案例按 Contract 需澄清，不强塞给 Worldline。此阶段不会修改原 Gold V2、Contract、Single criteria、生产阈值或生产路由。
