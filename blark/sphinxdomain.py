from __future__ import annotations

import dataclasses
import functools
import inspect
import logging
import textwrap
from typing import ClassVar, Dict

import jinja2
import sphinx
import sphinx.application
from docutils import nodes
from sphinx.directives import ObjectDescription
from sphinx.domains import Domain, ObjType
from sphinx.locale import _ as l_
from sphinx.roles import XRefRole
from sphinx.util.docfields import DocFieldTransformer, TypedField
from sphinx.util.docutils import SphinxTranslator
from sphinx.util.nodes import make_refnode

from . import summary
from .parse import parse

logger = logging.getLogger(__name__)

global_macros = {
    "html": (
        """
        {% macro make_permalink(identifier, type) %}
            {% if node.ids and translator.config.html_permalinks %}
                {% if  translator.builder.add_permalinks %}
                    <a class="headerlink" href="#{{ identifier }}"
                        title="Permalink to this {{ type }}">{{
                            translator.config.html_permalinks_icon
                    }}</a>
                {% endif %}
            {% endif %}
        {% endmacro %}
        """

        """
        {% macro node_source(node) %}
            <details>
                <summary>{{ node.ids[0] }} source code</summary>
                <div class="highlight">
                    <pre>{{ node.source_code }}</pre>
                </div>
                </summary>
            </details>
            <br />
        {% endmacro %}
        """

        """
        {% macro formatted_decl(decl) %}
            {% if app.config.blark_signature_show_type %}
                {% if decl.block == "OutputDeclarations" %}
                    {{ decl.type }} <em>{{decl.name}} =&gt;</em>
                {% else %}
                    {{ decl.type }} <em>{{decl.name}}</em>
                {% endif %}
            {% else %}
                {{ decl.name }}
            {% endif %}
        {% endmacro %}
        """

        """
        {% macro render_declarations(node, declarations) %}
            {% for block, decls in declarations.items() %}
                <dl class="field-list">
                {% if block == "InputDeclarations" %}
                    {% set block_name = "VAR_INPUT" %}
                {% elif block == "OutputDeclarations" %}
                    {% set block_name = "VAR_OUTPUT" %}
                {% elif block == "InputOutputDeclarations" %}
                    {% set block_name = "VAR_INOUT" %}
                {% elif block == "VariableDeclarations" %}
                    {% set block_name = "VAR" %}
                {% elif block == "MethodInstanceVariableDeclarations" %}
                    {% set block_name = "VAR_INST" %}
                {% else %}
                    {% set block_name = block %}
                {% endif %}
                <dt class="field-odd">{{ block_name }}</dt>
                <dd class="field-odd">
                    <dl class="{{ block_name | lower }}">
                    {% for decl in decls.values() %}
                        {% set qualified_name = node.name + "." + decl.name %}
                        <dt id="{{ qualified_name }}">
                            <span class="parameter">{{ decl.name }}</span>
                            &nbsp;:&nbsp;
                            <code class="typename">{{ decl.type }}</code>
                            {{ make_permalink(qualified_name, "variable") }}
                        </dt>
                        <dd>
                            {% if decl.value %}
                                <span class="paraminfo">Default: <code>{{decl.value}}</code></span>
                                <br />
                            {% endif %}
                            {% for comment in decl.comments %}
                                <span class="paraminfo">{{ comment | trim("/(*)") }}</span>
                            {% endfor %}
                            {% for pragma in decl.pragmas %}
                            <span class="pragma"><pre>{{ pragma }}</pre></span>
                            {% endfor %}
                        </dd>
                    {% endfor %}
                    </dl>
                </dd>
                </dl>
            {% endfor %}
        {% endmacro %}
        """
    )
}


@dataclasses.dataclass
class BlarkSphinxCache:
    cache: Dict[str, summary.CodeSummary] = dataclasses.field(default_factory=dict)
    _instance_: ClassVar[BlarkSphinxCache]

    @staticmethod
    def instance():
        if not hasattr(BlarkSphinxCache, "_instance_"):
            BlarkSphinxCache._instance_ = BlarkSphinxCache()
        return BlarkSphinxCache._instance_

    def find_by_name(self, name: str):
        for item in self.cache.values():
            try:
                return item.function_blocks[name]
            except KeyError:
                ...
        raise KeyError(f"{name!r} not found")

    def configure(self, app: sphinx.application.Sphinx, config):
        for filename in config.blark_projects:
            logger.debug("Loading %s", filename)
            for fn, info in parse(filename):
                logger.debug("Parsed %s", fn)
                self.cache[fn] = summary.CodeSummary.from_source(info)


class BlarkDirective(ObjectDescription):
    has_content = True
    required_arguments = 1
    optional_arguments = 0
    final_argument_whitespace = True
    doc_field_types = []

    def run(self):
        if ":" in self.name:
            self.domain, self.objtype = self.name.split(":", 1)
        else:
            self.domain, self.objtype = "", self.name

        obj_name = self.parse_arguments()
        scope = self.env.ref_context.get("bk:scope", [])
        docname = self.env.docname
        domaindata = self.env.domaindata["bk"]
        try:
            obj = BlarkSphinxCache.instance().find_by_name(obj_name)
        except KeyError:
            logger.error("Could not find: %s", obj_name)
            return []

        node = FunctionBlockNode.from_fb(obj)
        node.register(docname, scope, domaindata)
        DocFieldTransformer(self).transform_all(node)
        return [node]

    def parse_arguments(self):
        return self.arguments[0]

    def parse_content(self, modelnode):
        self.state.nested_parse(self.content, self.content_offset, modelnode)


# class Module(BlarkDirective):
#     final_argument_whitespace = False
#
#     def parse_content(self, modelnode):
#         if "bk:scope" not in self.env.ref_context:
#             self.env.ref_context["bk:scope"] = []
#         self.env.ref_context["bk:scope"].append(modelnode.name)
#         super().parse_content(modelnode)
#         self.env.ref_context["bk:scope"].pop()


class FunctionBlock(BlarkDirective):
    doc_field_types = [
        TypedField(
            "parameter",
            label=l_("Parameters"),
            names=("param", "parameter", "arg", "argument"),
            typerolename="obj",
            typenames=("paramtype", "type"),
            can_collapse=True,
        ),
        # Field(
        #     "returntype",
        #     label=l_("Return type"),
        #     has_arg=False,
        #     names=("rtype",),
        #     bodyrolename="obj",
        # ),
    ]


# class Type(BlarkDirective):
#     pass


class BlarkXRefRole(XRefRole):
    def process_link(self, env, refnode, has_explicit_title, title, target):
        refnode["bk:scope"] = list(env.ref_context.get("bk:scope", []))
        if not has_explicit_title:
            title = title.lstrip(".")  # only has a meaning for the target
            target = target.lstrip("~")  # only has a meaning for the title
            # if the first character is a tilde, don't display the module/class
            # parts of the contents
            if title[0:1] == "~":
                title = title[1:]
                dot = title.rfind(".")
                if dot != -1:
                    title = title[dot + 1:]
        return title, target


class BlarkDomain(Domain):
    """
    Blark IEC61131-3 language domain.
    """
    name = "bk"
    label = "Blark"
    object_types = {
        "functionblock": ObjType(l_("functionblock"), l_("func"), l_("fb")),
        "type": ObjType(l_("type"), "type"),
        "module": ObjType(l_("module"), "mod"),
        "parameter": ObjType(l_("parameter"), "parameter"),
    }

    directives = {
        "functionblock": FunctionBlock,
        # "type": Type,
        # "module": Module,
    }

    roles = {
        "functionblock": BlarkXRefRole(fix_parens=False),
        "fb": BlarkXRefRole(fix_parens=False),
        "parameter": BlarkXRefRole(),
        "type": BlarkXRefRole(),
        "mod": BlarkXRefRole(),
    }

    initial_data = {
        # name -> [{docname, scope, qualified_name}, ...]
        "module": {},
        "type": {},
        # name -> [{docname, scope, templateparameters, signature, qualified_name}]
        "functionblock": {},
        "parameter": {},
        "method": {},
        "action": {},
    }
    indices = [
        # BlarkModuleIndex,
    ]

    def find_obj(self, rolename, node, targetstring):
        for typename, objtype in self.object_types.items():
            if rolename in objtype.roles:
                break
        else:
            return []
        # TODO: scoping?
        # basescope = node["bk:scope"]
        domaindata = self.env.domaindata["bk"][typename]
        return domaindata.get(targetstring, [])

    def resolve_xref(self, env, fromdocname, builder, typ, target, node, contnode):
        matches = self.find_obj(typ, node, target)
        if not matches:
            logger.warning("No target found for cross-reference: %s", target)
            return None
        if len(matches) > 1:
            logger.warning(
                "More than one target found for cross-reference " "%r: %s",
                target,
                ", ".join(match["qualified_name"] for match in matches),
            )
        match = matches[0]
        return make_refnode(
            builder, fromdocname, match["docname"], match["qualified_name"],
            contnode, target
        )

    def clear_doc(self, docname):
        dictionaries = self.env.domaindata["bk"]
        for dicname in self.initial_data.keys():
            dictionary = dictionaries[dicname]
            for name, methods in dictionary.items():
                items_to_delete = []
                for i, m in enumerate(methods):
                    if m["docname"] == docname:
                        items_to_delete.insert(0, i)
                for i in items_to_delete:
                    methods.pop(i)


def setup(app: sphinx.application.Sphinx):
    app.add_config_value('blark_projects', [], 'html')
    app.add_config_value('blark_signature_show_type', True, 'html')

    for cls in (FunctionBlockNode, ParameterNode, ActionNode, MethodNode):
        app.add_node(
            cls,
            html=(
                functools.partial(render_block, app, "html", 0),
                functools.partial(render_block, app, "html", 1),
            ),
        )
    app.add_domain(BlarkDomain)
    app.connect("config-inited", BlarkSphinxCache.instance().configure)


def render_block(
    app: sphinx.application.Sphinx,
    format: str,
    template_index: int,
    translator: SphinxTranslator,
    node: nodes.Element,
):
    template = textwrap.dedent(node._jinja_format_[format][template_index])
    if hasattr(node, "get_render_context"):
        ctx = node.get_render_context(translator, format)
    else:
        ctx = {}
    formatted = FormatContext().render_template(
        global_macros.get(format, "") + template,
        node=node,
        translator=translator,
        app=app,
        **ctx
    )
    translator.body.append(formatted)


pass_eval_context = (
    jinja2.pass_eval_context
    if hasattr(jinja2, "pass_eval_context")
    else jinja2.evalcontextfilter
)


class FormatContext:
    def __init__(
        self, helpers=None, *, trim_blocks=True, lstrip_blocks=False, **env_kwargs
    ):
        self.helpers = helpers or [type, locals]
        self.env = jinja2.Environment(
            trim_blocks=trim_blocks,
            lstrip_blocks=lstrip_blocks,
            **env_kwargs,
        )

        self.env.filters.update(self.get_filters())
        self.default_render_context = self.get_render_context()

    def get_filters(self, **user_config):
        """Default jinja filters for all contexts."""

        @pass_eval_context
        def title_fill(eval_ctx, text, fill_char):
            return fill_char * len(text)

        @pass_eval_context
        def classname(eval_ctx, obj):
            if inspect.isclass(obj):
                return obj.__name__
            return type(obj).__name__

        return {
            key: value
            for key, value in locals().items()
            if not key.startswith("_") and key not in {"self"}
        }

    def render_template(self, _template: str, **context):
        # TODO: want this to be positional-only; fallback here for pypy
        template = _template

        for key, value in self.default_render_context.items():
            context.setdefault(key, value)
        context["render_ctx"] = context
        return self.env.from_string(template).render(context)

    def get_render_context(self) -> dict:
        """Jinja template context dictionary - helper functions."""
        context = {func.__name__: func for func in self.helpers}
        return context


class BlarkNode(nodes.Element):
    @property
    def name(self):
        return self["ids"][0].split(".")[-1]

    @property
    def qualified_name(self):
        return self["ids"][0]

    def register(self, docname, scope, domaindata):
        item = {
            "docname": docname,
            "scope": list(scope),
            "qualified_name": self.qualified_name,
        }

        # domaindata.setdefault(self.name, []).append(item)
        # if self.qualified_name != self.name:
        domaindata[self.objtype].setdefault(self.qualified_name, []).append(item)


class ParameterNode(BlarkNode):
    objtype: str = "parameter"
    _jinja_format_ = {
        "html": ("", ""),
    }

    @classmethod
    def from_decl(
        cls, owner: summary.FunctionBlockSummary, decl: summary.DeclarationSummary
    ) -> ParameterNode:
        return cls(
            name=decl.name,
            owner=owner,
            decl=decl,
            ids=[f"{owner.name}.{decl.name}"],
        )


class ElementWithDeclarations(BlarkNode):
    def get_render_context(self, translator: SphinxTranslator, format: str):
        decls = self["declarations"]
        inputs = dict(decls.get("InputDeclarations", {}))
        inputs.update(decls.get("InputOutputDeclarations", {}))
        outputs = decls.get("OutputDeclarations", {})
        return dict(
            inputs=list(inputs.values()),
            outputs=list(outputs.values()),
        )

    def register(self, docname, scope, domaindata):
        super().register(docname, scope, domaindata)
        for child in self.children:
            if isinstance(child, BlarkNode):
                child.register(docname, scope, domaindata)


class ActionNode(BlarkNode):
    objtype: str = "action"

    _jinja_format_ = {
        "html": (
            """\
            <dl class="function">
                <dt id="{{ node.name }}">
                    <span class="sig">
                        <em class="property">ACTION</em>
                        <code class="descname">
                            {{ node.name }}
                        </code>
                        {{ make_permalink(node.name, "action") }}
                        </dt>
                    </span>
                    <dd>
                    {% if node.declarations %}
                        {{ render_declarations(node, node.declarations) }}
                    {% endif %}
                    </dd>
                    {{ node_source(node) }}
            """,

            """\
            </dd></dl>
            """
        )
    }

    @classmethod
    def from_action(
        cls, fb: summary.FunctionBlockSummary, action: summary.ActionSummary
    ) -> ActionNode:
        return cls(
            name=action.name,
            ids=[f"{fb.name}.{action.name}"],
            source_code=action.source_code,
        )


class MethodNode(ElementWithDeclarations):
    objtype: str = "method"

    _jinja_format_ = {
        "html": (
            """\
            <dl class="function">
                <dt id="{{ node.name }}">
                    <span class="sig">
                        <em class="property">METHOD</em>
                        <code class="descname">
                            {{ node.name }}
                        </code>
                        {% set formatted_decls = [] %}
                        {% for decl in inputs + outputs %}
                            {% set _ = formatted_decls.append(formatted_decl(decl)) %}
                        {% endfor %}
                        <span class="sig-paren">(</span>{{
                            formatted_decls | join(", ")
                        }}<span class="sig-paren">)</span>

                        {{ make_permalink(node.name, "function block method") }}
                        </dt>
                    </span>
                    <dd>
                    {% if node.declarations %}
                        {{ render_declarations(node, node.declarations) }}
                    {% endif %}
                    </dd>
                    {{ node_source(node) }}
            """,

            """\
            </dd></dl>
            """
        )
    }

    @classmethod
    def from_method(
        cls, fb: summary.FunctionBlockSummary, method: summary.MethodSummary
    ) -> MethodNode:
        children = [
            ParameterNode.from_decl(fb, decl)
            for block, decls in method.declarations_by_block.items()
            for decl in decls.values()
        ]
        return cls(
            *children,
            name=method.name,
            ids=[f"{fb.name}.{method.name}"],
            declarations=method.declarations_by_block,
            source_code=method.source_code,
        )


class FunctionBlockNode(ElementWithDeclarations):
    objtype: str = "functionblock"

    _jinja_format_ = {
        "html": (
            """\
            <dl class="function">
                <dt id="{{ node.name }}">
                    <span class="sig">
                        <em class="property">FUNCTION_BLOCK</em>
                        <code class="descname">
                            {{ node.name }}
                        </code>
                        {% set formatted_decls = [] %}
                        {% for decl in inputs + outputs %}
                            {% set _ = formatted_decls.append(formatted_decl(decl)) %}
                        {% endfor %}
                        <span class="sig-paren">(</span>{{
                            formatted_decls | join(", ")
                        }}<span class="sig-paren">)</span>

                        {{ make_permalink(node.name, "function block") }}
                        </dt>
                    </span>
                    <dd>
                    {% if node.declarations %}
                        {{ render_declarations(node, node.declarations) }}
                    {% endif %}
                    </dd>
                    {{ node_source(node) }}
            """,

            """\
            </dd></dl>
            """
        )
    }

    @classmethod
    def from_fb(cls, fb: summary.FunctionBlockSummary) -> FunctionBlockNode:
        children = [
            ParameterNode.from_decl(fb, decl)
            for block, decls in fb.declarations_by_block.items()
            for decl in decls.values()
        ]
        children.extend(
            [ActionNode.from_action(fb, action) for action in fb.actions]
        )
        children.extend(
            [MethodNode.from_method(fb, method) for method in fb.methods]
        )
        return cls(
            *children,
            name=fb.name,
            ids=[fb.name],
            declarations=fb.declarations_by_block,
            source_code=fb.source_code,
        )
