---
source: https://github.com/AofeiLi-code/rocom-data
imported: 2026-05-07
---

# rocom-data 导入数据

## 数据文件

| 文件 | 位置 | 条数 | 说明 |
|------|------|------|------|
| skills_all.csv | `wiki/meta/skills_all.csv` | 487 | 全技能数据（名称/属性/类型/威力/能耗/效果/应对） |
| sprites.csv | `wiki/meta/sprites.csv` | 468 | 全精灵数据（编号/名称/形态/六维/特性/克制/技能列表） |
| teams.json | → 阵容页面 | 5 队 | 预设和用户导入队伍配置 |

## 阵容页面

- [[热门阵容/预设毒队]] — 千棘盔/影狸/裘卡/琉璃水母/迷迷箱怪/海豹船长
- [[热门阵容/预设翼王队]] — 燃薪虫/圣羽翼王/翠顶夫人/迷迷箱怪/秩序鱿墨/声波缇塔
- [[热门阵容/狼王队]] — 画间沉铁兽/卡瓦重/恶魔狼/翼龙/翠顶夫人/音速犬
- [[热门阵容/平衡狼王队]] — 黑猫巫师/白金独角兽/卡瓦重/朔夜伊芙/帕帕斯卡/恶魔狼
- [[热门阵容/沙暴武]] — 棋绮后/巨噬针鼹/食尘短绒/画间沉铁兽/帕帕斯卡/小皮球

## 数据字段说明

### skills_all.csv

`技能名,属性,类型,威力,耗能,效果描述,所属精灵,数据来源,备注`

### sprites.csv

`no,name,form,url,has_shiny,attributes,total_stats,hp,atk,sp_atk,def,sp_def,spd,ability_name,ability_desc,strong_against,weak_to,resists,resisted_by,skills`

> 来源：AofeiLi-code/rocom-data，用于战斗模拟的权威游戏数据。
