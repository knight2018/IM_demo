"""通道命令与自动路由；选择、生成和记录在同一用户会话锁内完成。"""

import json
import threading
from dataclasses import replace
from collections.abc import Callable

from core.api_chat import ApiChat
from core.codex import CodexBridge
from core.routing_store import Backend, ModelSettings, RoutingStore

USAGE = ('/use_model api|codex|auto 切换通道\n'
         '/model 查看当前选择\n/model <模型ID> 切换当前固定通道的模型\n'
         '/model api <模型ID> 或 /model codex <模型ID> 分别设置\n'
         '/model list api|codex [关键词] 查看模型目录\n'
         'auto：简单问题使用 API，复杂问题使用 Codex；Codex 推理强度保持 medium。')


class ModelRouter:
    def __init__(self, store: RoutingStore, api: ApiChat, codex: CodexBridge) -> None:
        self.store, self.api, self.codex = store, api, codex
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _lock(self, conversation: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(conversation, threading.Lock())

    @staticmethod
    def status(settings: ModelSettings) -> str:
        return (f'当前通道：{settings.mode}\nAPI：{settings.api_model}\n'
                f'Codex：{settings.codex_model} / medium\n设置仅对你在当前飞书会话中生效。')

    def _models(self, backend: Backend) -> list[str]:
        return self.api.models() if backend == 'api' else self.codex.available_models()

    def command(self, conversation: str, text: str) -> str:
        args = text.strip().split()
        with self._lock(conversation):
            settings = self.store.get(conversation)
            if len(args) == 1:
                return self.status(settings)+'\n\n'+USAGE
            if args[0] == '/use_model':
                if len(args) != 2 or args[1] not in {'api', 'codex', 'auto'}:
                    return '通道只能是 api、codex 或 auto。\n'+USAGE
                updated = replace(settings, mode=args[1])
                self.store.save(conversation, updated)
                return self.status(updated)
            if args[0] != '/model':
                return USAGE
            if args[1] == 'list':
                if len(args) not in {3, 4} or args[2] not in {'api', 'codex'}:
                    return USAGE
                models = self._models(args[2])
                if len(args) == 4:
                    models = [model for model in models if args[3].lower() in model.lower()]
                return (f'{args[2]} 模型目录（{len(models)} 项，最多显示 40 项）：\n'+'\n'.join(models[:40])+
                        '\n目录可见不代表所有模型都支持文字对话或当前账号可调用。可添加关键词筛选。')
            if len(args) == 2:
                if settings.mode == 'auto':
                    return 'auto 模式需要指定修改哪条通道：/model api <模型ID> 或 /model codex <模型ID>'
                backend, model = settings.mode, args[1]
            elif len(args) == 3 and args[1] in {'api', 'codex'}:
                backend, model = args[1], args[2]
            else:
                return USAGE
            if model not in self._models(backend):
                return f'{backend} 目录中未找到该模型（Codex 还需支持 medium），原设置未改变。'
            updated = replace(settings, **{backend+'_model': model})
            self.store.save(conversation, updated)
            return '模型选择已保存。\n'+self.status(updated)

    def answer(self, conversation: str, text: str,
               on_update: Callable[[str], None] | None = None) -> str:
        with self._lock(conversation):
            settings = self.store.get(conversation)
            history = self.store.history(conversation)
            backend = settings.mode
            route_note = ''
            if backend == 'auto':
                decision = self.api.classify(settings.api_model, text, history)
                backend = 'codex' if decision.difficulty == 'complex' else 'api'
                route_note = f'auto → {backend}；{decision.reason}'
            model = settings.api_model if backend == 'api' else settings.codex_model
            if backend == 'api':
                reply = (self.api.answer(model, text, history) if on_update is None
                         else self.api.answer(model, text, history, on_update=on_update))
            else:
                # 保持原 Codex 线程，同时补齐 API 通道的最近记录，切换后仍能理解指代。
                prompt = text if not history else ('以下 JSON 是最近对话资料，可能与已有记录重叠；'
                    '按顺序理解资料，仅回答 current_question：\n'+json.dumps(
                        {'recent_dialogue': history, 'current_question': text}, ensure_ascii=False))
                request = {'conversation_key': conversation, 'text': prompt, 'model': model}
                reply = (self.codex.reply(request) if on_update is None
                         else self.codex.reply(request, on_update=on_update))
            self.store.append(conversation, text, reply)
            label = route_note or backend
            return f'{reply}\n\n[通道：{label} | 模型：{model}'+(' | medium' if backend == 'codex' else '')+']'
