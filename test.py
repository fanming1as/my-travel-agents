from typing import Annotated,Sequence,TypedDict
from dotenv import load_dotenv
from langchain_core.messages import BaseMessage
from langchain_core.messages import ToolMessage
from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langgraph.graph.message import add_messages
from langgraph.graph import StateGraph,END
from langgraph.prebuilt import ToolNode

load_dotenv()
import os
actual_api_key = os.getenv("LLM_API_KEY")
actual_base_url = os.getenv("LLM_BASE_URL")
actual_model = os.getenv("LLM_MODEL_ID")
class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage],add_messages]

@tool
def add(a:int,b:int):
    """计算两个数的和"""
    return a+b

tools=[add]

model=ChatOpenAI(model=actual_model,api_key=actual_api_key,base_url=actual_base_url).bind_tools(tools)

def model_call(state:AgentState)->AgentState:
    system_prompt = SystemMessage(content="你是一个ai助手，尽你所能回答我的问题。")
    response = model.invoke([system_prompt]+state["messages"])
    return {"messages":[response]}

def should_continue(state:AgentState)->AgentState:
    messages = state["messages"]
    last_message = messages[-1]
    if not last_message.tool_calls:
        return "end"
    else:
        return "continue"
graph = StateGraph(AgentState)
graph.add_node("our_agent",model_call)
tool_node = ToolNode(tools=tools)
graph.add_node("tools",tool_node)
graph.set_entry_point("our_agent")
graph.add_conditional_edges(
    "our_agent",
    should_continue,
    {
        "continue":"tools",
        "end":END,
    },
)
graph.add_edge("tools","our_agent")
app=graph.compile()
def print_stream(stream):
    for s in stream:
        message = s["messages"][-1]
        if isinstance(message,tuple):
            print(message)
        else:
            message.pretty_print()

#from IPython.display import Image,display
#display(Image(app.get_graph().draw_mermaid_png()))

inputs = {"messages":[("user","计算40+12，将结果乘以3，再告诉我一个笑话.")]}
print_stream(app.stream(inputs,stream_mode="values"))
