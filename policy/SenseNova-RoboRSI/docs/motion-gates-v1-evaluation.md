# motion-gates-v1 候选与已观测样例

此分支保存 B 区实际评测过的完整候选版本。stack_blocks 与 fold_clothes 对应样例和分组有正向观测，尚未验证稳定或总体提升；不是下轮新拼接变体，也没有部署。

源码基线为 `a838046dbb4bc868cc46d2186ab00194b005a5de`。先单独保存两组共用的评测前置修复，再保存完整 motion-gates-v1 skill treatment。827 个 policy 文件与冻结候选源码包逐字一致，54 题离线 skill 契约/SHA 校验通过。冻结候选包 SHA256：`f45397723535029f211c28779297963c20e1c4ac6afe026e2d29b7a7bd43b91a`；冻结 baseline 包 SHA256：`6c88a71317b8c608a7c2ead6d60bf48ab566906ac52336f2fdbd4e4c45572eca`。

## 同场结果

快照：B 2026-11-06 13:39:41，E 2026-11-06 13:40:00。按题目、seed、layout、scene SHA 严格配对，随机变体按冻结 catalog 映射到基题 skill，所有改过的组及 general-only 组均列出。成功数均为正式 native 场次，不把未评分中断算失败。

| Skill | 题目 | 配对 | E成功 | B成功 | B独胜/E独胜 | B−E百分点 | 平均native分数差 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| arrange_largest_number | arrange_largest_number, arrange_largest_number_random | 26 | 17 | 16 | 5/6 | -3.85 | -0.0327 |
| fill_egg_holder | fill_egg_holder | 13 | 0 | 0 | 0/0 | +0.00 | -0.0923 |
| fill_pen_holder | fill_pen_holder | 5 | 0 | 0 | 0/0 | +0.00 | -0.0300 |
| fold_clothes | fold_clothes, fold_clothes_random | 28 | 14 | 17 | 7/4 | +10.71 | +0.0857 |
| make_toast | make_toast, make_toast_random | 9 | 5 | 3 | 1/3 | -22.22 | -0.2500 |
| stack_blocks | stack_blocks, stack_blocks_random | 28 | 14 | 19 | 6/1 | +17.86 | +0.1518 |
| robo-general-only | cover_blocks, match_and_pick_from_conveyor | 27 | 13 | 4 | 4/13 | -33.33 | -0.3167 |

六个任务 skill 的联合暴露组共109对，E50/B55成功，+4.59个百分点，探索性配对bootstrap 95%区间[-5.50,+14.68]；全部136对则E63/B59，−2.94个百分点。stack_blocks组p=.125、fold_clothes组p=.549；7个分组的多重比较亦不支持宣称某组稳定提升。完整均值、配对胜负、区间和按题拆分见相邻JSON。

## 正反样例

- stack_blocks，seed0/layout23：B官方1.0、498/550步done；E官方0.15、550/550步。原始末帧中B三块已成柱、双臂撤开，E仍有分散块与操作中的左手。stack_blocks_random seed1/layout2是反例：B0.15、550步且中间两块仍分开，E1.0、488步且三块成柱；双方原始API/图像均已核对。
- fold_clothes，seed2/layout16：B1.0、442/500步done，衣物较紧凑；E0.2、500/500步，衣物仍较展开。seed0/layout41是反例，原始末帧B更展开而E更收拢。random变体及其他任务的正反样例一并保存在JSON，未按结果只挑正例。
- fold_clothes_random，seed0/layout18：B356步衣物已收拢并撤手，E到500步仍拉着展开衣物。分题统计必须保留：普通fold_clothes为E9/B8成功，random为E5/B9；stack_blocks普通题E9/B13，random为E5/B6。
- make_toast，seed1/layout38负例：B末帧面包在机外并到1400步；E槽内可见面包，1005步done。静态图不能单独证明此前压杆行程。

原始API捕获、结果及RGB字节逐个按SHA校验；候选/原版skill正文确实出现在抽检的forwarded输入中。分析用的部分B缓存仅覆盖训练输入，不等于完整轨迹已经回传；没有用它放宽原始数据删除条件。

## 归因与运行差异

任务skill与robo-general同时改变，无法单独归因。E还有早期skill版本、续跑和刷新历史。136对的模型及动作参数、逐题native预算已核对相同；这不等于所有历史运行配置相同。

execution_profile.py、native_curobo.py、prompt_profile.py与E当前源码相同，它们是共用前置，不计作skill收益。B两臂冻结deploy.yml的heartbeat timeout为3660秒；E当前源码为20秒，尚未逐条证明E历史attempt实际采用值。该部署差异保留为潜在运行混杂。deploy.yml没有私有provider绑定或凭据。

所有native guard、动作预算、模型/tool参数保持冻结评测版本；native_curobo pin仍为`46ad5fc1c8a0182e622d5e5a0e518b83085e251d24da90fa557547f20e89996e`。这里只整理本地可审阅候选，无新provider或仿真调用，无推送、PR、队列变更或源轨迹删除。
