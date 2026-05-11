# _node_knowledge_retrieval 节点说明

## 作用概述

`_node_knowledge_retrieval` 是旅行规划流程中的知识检索节点。它的主要职责是在正式生成行程之前，根据用户请求补充两类上下文信息：

- `user_memory`：用户过往反馈、偏好和经验记忆。
- `rag_knowledge`：从语义知识库中检索到的城市相关知识。

后续的 POI 选择、信息收集和行程规划节点可以基于这些信息生成更贴近用户偏好、更有本地知识支撑的旅行计划。

## 输入

该节点接收一个 `state` 字典，核心字段是：

```python
state = {
    "request": TripRequest,
    "user_id": "用户 ID，可选"
}
```

节点会从 `request` 中读取以下信息：

```python
city = getattr(request, "city", "")
prefs = getattr(request, "preferences", [])
free_text = getattr(request, "free_text_input", "")
```

含义分别是：

- `city`：用户想去的城市。
- `preferences`：用户选择的旅行偏好，例如美食、自然风光、文化体验等。
- `free_text_input`：用户额外输入的自由文本要求。
- `user_id`：用于查询该用户的历史经验记忆；如果没有传入，则使用 `default_guest`。

## 处理流程

### 1. 读取用户请求

```python
request = state["request"]
user_id = state.get("user_id", "default_guest")
```

这里从流程状态中取出当前旅行请求，并确定用户身份。`request` 是必需字段，`user_id` 是可选字段。

### 2. 提取检索关键词

```python
city = getattr(request, "city", "")
prefs = getattr(request, "preferences", [])
free_text = getattr(request, "free_text_input", "")
```

使用 `getattr` 可以避免某些字段不存在时直接报错。字段不存在时会使用默认值。

### 3. 查询用户经验记忆

```python
user_memory = self.episodic_memory.recall_lessons(
    user_id, f"去{city}，偏好:{prefs}，要求:{free_text}"
)
```

这一段查询的是“情节记忆”或“经验记忆”。它通常来自用户历史反馈，例如：

- 用户以前喜欢或不喜欢的景点类型。
- 用户对节奏、预算、交通方式的历史偏好。
- 用户曾经给出的评价和建议。

检索条件由城市、偏好和自由文本要求组合而成，用来找到和当前请求相似的历史经验。

### 4. 查询语义知识库

```python
query_text = f"{city} 景点 餐厅 酒店 {' '.join(prefs)} {free_text} {user_memory}"
results = await self.semantic_memory.search_knowledge_async(query_text, k=4)
```

这一段查询的是语义知识库，也就是 RAG 检索。它会根据城市、景点/餐厅/酒店关键词、用户偏好、自由文本和用户记忆，找出最相关的知识片段。

`k=4` 表示最多取 4 条相关结果。这里一次性检索后续 POI 选择需要的上下文，避免后面的节点再次查询 Qdrant。

### 5. 拼接 RAG 知识

```python
rag_knowledge = "\n\n".join(content for _, content in results)
```

`results` 中每条结果通常包含相似度信息和文本内容。这里忽略相似度，只取 `content`，并用空行拼接成一段完整上下文。

## 输出

节点最终返回：

```python
return {
    "rag_knowledge": rag_knowledge,
    "user_memory": user_memory
}
```

这两个字段会被合并回后续流程的 `state` 中，供后面的节点使用。

## 在整体流程中的位置

在 `plan_trip` 中，该节点是较早执行的上下文增强步骤：

```python
memory_payload = await self._node_knowledge_retrieval(initial_state)
selected_pois = await self._node_poi_selector({**initial_state, **memory_payload})
```

也就是说，它先检索知识和用户记忆，然后把结果传给 POI 选择节点。后续节点可以根据这些内容选择更合适的地点和规划策略。

## 简单理解

这个节点的作用可以概括为：

> 在生成旅行计划前，先查“这个用户以前有什么偏好”和“知识库里有哪些当前目的地相关信息”，再把这些内容交给后续规划节点使用。

它让旅行计划不只是根据当前请求临时生成，而是能结合历史用户记忆和外部知识库内容。

## 注意事项

- `state["request"]` 是必需的，如果不存在会抛出 `KeyError`。
- `user_id` 没有传入时会统一落到 `default_guest`，多个匿名用户可能共享同一类默认记忆。
- `semantic_memory.search_knowledge_async(..., k=4)` 会一次性取 4 条知识，供后续 POI 选择和规划节点复用。
- 当前 RAG 检索 query 已经拼入 `preferences`、`free_text` 和 `user_memory`，更适合召回和用户偏好相关的景点、餐厅、酒店内容。

# _node_poi_selector 节点说明

## 作用概述

`_node_poi_selector` 是旅行规划流程中的 POI 选择节点。POI 是 `Point of Interest` 的缩写，通常指景点、餐厅、酒店、商圈等旅行中可能被安排进路线的地点。

当前代码里的这个节点会根据用户请求、RAG 检索结果和用户历史记忆，生成三类高德搜索词：

- `attractions`：景点搜索词，例如“博物馆”“夜景”“热门景点”。
- `restaurants`：餐厅搜索词，例如“本地小吃”“老字号餐厅”。
- `hotels`：酒店搜索词，例如“交通便利酒店”“景区附近酒店”。

## 输入

该节点接收一个 `state` 字典，至少需要包含：

```python
state = {
    "request": TripRequest
}
```

在实际调用时，它接收到的是 `initial_state` 和 `memory_payload` 合并后的结果：

```python
selected_pois = await self._node_poi_selector({
    **initial_state,
    **memory_payload
})
```

因此理论上这个节点可以读取：

```python
state["request"]
state["rag_knowledge"]
state["user_memory"]
```

当前实现会读取：

```python
request = state["request"]
rag_knowledge = state.get("rag_knowledge", "")
user_memory = state.get("user_memory", "")
```

也就是说，这个节点已经会利用上一节点返回的通用知识和用户记忆。

## 处理流程

### 1. 读取用户请求

```python
request = state["request"]
```

这里从流程状态中取出当前旅行请求。后面会根据请求中的偏好、住宿类型和自由文本生成搜索词。

### 2. 合并上下文

```python
context = "\n".join(part for part in (rag_knowledge, user_memory) if part)
```

这里把上一节点传来的 `rag_knowledge` 和用户记忆 `user_memory` 合并成一段文本，作为判断用户偏好的上下文。POI 节点不再重复查询 Qdrant。

### 3. 从上下文生成高德搜索词

```python
search_terms = self._build_poi_search_terms(request, context)
```

这一段不再从文本里硬提取具体地点名，而是从用户偏好里提取搜索意图。

例如用户偏好和上下文中出现：

```text
想多安排博物馆和老字号餐厅，晚上看看夜景
```

节点会生成：

```python
{
    "attractions": ["博物馆", "历史文化景点", "夜景", "观景台", "热门景点"],
    "restaurants": ["本地小吃", "特色餐厅", "老字号餐厅", "热门餐厅", "本地菜"],
    "hotels": ["舒适型酒店", "交通便利酒店", "景区附近酒店"]
}
```

这些词会交给 `_node_gather_info`，由高德地图返回真实 POI。

### 4. 兜底候选项

节点仍然会使用 `_fallback_pois` 生成兜底候选。没有高德 Key 或高德查询失败时，后续节点会使用这些兜底内容保证流程可继续。

例如：

```python
[
    "上海城市地标",
    "上海历史文化街区",
    "上海夜景打卡点"
]
```

如果用户偏好里包含“亲子”“迪士尼”“博物馆”“自然”等词，兜底结果也会相应调整。

## 输出

该节点最终返回一个字典：

```python
{
    "poi_search_terms": {
        "attractions": [...],
        "restaurants": [...],
        "hotels": [...]
    },
    "selected_pois": {
        "attractions": [...],
        "restaurants": [...],
        "hotels": [...]
    }
}
```

这个结果会在 `plan_trip` 中继续传给后面的信息收集节点：

```python
mcp_data = await self._node_gather_info({
    **initial_state,
    **memory_payload,
    **selected_pois
})
```

合并后，信息补全节点会优先从 `state["poi_search_terms"]` 中拿高德搜索词；如果不能调用高德，则使用 `state["selected_pois"]` 中的兜底候选。

# _node_gather_info 节点说明

## 作用概述

`_node_gather_info` 是信息补全节点。它会优先读取 POI 选择节点生成的 `poi_search_terms`，调用高德地图查询真实结构化 POI；如果没有搜索词或高德不可用，则使用 `selected_pois` 兜底。

当前实现会优先调用高德地图接口：

- 景点、餐厅、酒店的地址。
- 经纬度。
- POI ID。
- 类型。
- 电话。
- 评分。
- 商圈。
- 城市天气预报。

如果没有配置 `AMAP_API_KEY`，或者接口查询失败，节点会返回本地兜底信息，保证后续流程还能继续执行。

## 输入

```python
state = {
    "request": TripRequest,
    "poi_search_terms": {
        "attractions": [...],
        "restaurants": [...],
        "hotels": [...]
    },
    "selected_pois": {
        "attractions": [...],
        "restaurants": [...],
        "hotels": [...]
    }
}
```

## 输出

```python
return {
    "mcp_data": {
        "city": city,
        "pois": {
            "attractions": [...],
            "restaurants": [...],
            "hotels": [...]
        },
        "weather": [...],
        "warnings": [...]
    }
}
```

`warnings` 会记录高德 Key 未配置、某个 POI 没查到、接口请求失败等问题。

## 简单理解

这个节点的作用可以概括为：

> 根据用户要去的城市，先准备一组候选旅行地点，供后面的信息补全和行程规划节点使用。

当前代码已经不再只是返回“上海代表景点”这类固定模板，而是会优先从知识库文本和用户记忆中提取候选地点。

## 当前实现的局限

- POI 提取是基于文本规则和关键词，不如地图 API 或结构化 POI 数据稳定。
- 如果知识库内容质量较差，提取出来的候选地点也会受影响。
- `_node_gather_info` 已接入高德 POI 和天气查询，但酒店价格、实时库存、点评内容仍需要专门的数据源。

如果后续要继续增强这个节点，可以接入酒店数据源、点评数据源或大模型结构化抽取。
