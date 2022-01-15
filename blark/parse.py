"""
`blark parse` is a command-line utility to parse TwinCAT3 source code
files in conjunction with pytmc.
"""
import argparse
import pathlib
import sys
import traceback
from typing import Any, Generator, Optional, Tuple, Union

import lark
import pytmc

import blark

from . import summary
from . import transform as tf
from .transform import GrammarTransformer
from .util import (AnyPath, find_and_clean_comments, get_source_code,
                   python_debug_session)

DESCRIPTION = __doc__
AnyFile = Union[str, pathlib.Path]

_PARSER = None


def new_parser(**kwargs) -> lark.Lark:
    """
    Get a new parser for TwinCAT flavor IEC61131-3 code.

    Parameters
    ----------
    **kwargs :
        See :class:`lark.lark.LarkOptions`.
    """
    return lark.Lark.open_from_package(
        "blark",
        blark.GRAMMAR_FILENAME.name,
        parser="earley",
        maybe_placeholders=True,
        propagate_positions=True,
        **kwargs
    )


def get_parser() -> lark.Lark:
    """Get a cached lark.Lark parser for TwinCAT flavor IEC61131-3 code."""
    global _PARSER

    if _PARSER is None:
        _PARSER = new_parser()
    return _PARSER


DEFAULT_PREPROCESSORS = []
_DEFAULT_PREPROCESSORS = object()


def parse_source_code(
    source_code: str,
    *,
    verbose: int = 0,
    fn: str = "unknown",
    preprocessors=_DEFAULT_PREPROCESSORS,
    parser: Optional[lark.Lark] = None,
    transform: bool = True,
):
    """
    Parse source code and return the transformed result.

    Parameters
    ----------
    source_code : str
        The source code text.

    verbose : int, optional
        Verbosity level for output.

    fn : str, optional
        The filename associated with the source code.

    preprocessors : list, optional
        Callable preprocessors to apply to the source code.

    parser : lark.Lark, optional
        The parser instance to use.  Defaults to the global shared one from
        ``get_parser``.

    transform : bool, optional
        If True, transform the output into blark-defined Python dataclasses.
        Otherwise, return the ``lark.Tree`` instance.
    """
    if preprocessors is _DEFAULT_PREPROCESSORS:
        preprocessors = DEFAULT_PREPROCESSORS

    processed_source = source_code
    for preprocessor in preprocessors:
        processed_source = preprocessor(processed_source)

    comments, processed_source = find_and_clean_comments(processed_source)
    if parser is None:
        parser = get_parser()

    try:
        tree = parser.parse(processed_source)
    except Exception as ex:
        if verbose > 1:
            print("[Failure] Parse failure")
            print("-------------------------------")
            print(source_code)
            print("-------------------------------")
            print(f"{type(ex).__name__} {ex}")
            print(f"[Failure] {fn}")
        raise

    if verbose > 2:
        print(f"Successfully parsed {fn}:")
        print("-------------------------------")
        print(source_code)
        print("-------------------------------")
        print(tree.pretty())
        print("-------------------------------")
        print(f"[Success] End of {fn}")

    if transform:
        transformer = GrammarTransformer(
            comments=comments,
            fn=fn,
            source_code=source_code,
        )
        return transformer.transform(tree)
    return tree


def parse_single_file(fn, *, transform: bool = True):
    """Parse a single source code file."""
    source_code = get_source_code(fn)
    return parse_source_code(source_code, fn=fn, transform=transform)


def parse_project(
    tsproj_project: AnyFile,
    *,
    verbose: int = 0,
    transform: bool = True
) -> Generator[Tuple[pathlib.Path, Any], None, None]:
    """Parse an entire tsproj project file."""
    proj_path = pathlib.Path(tsproj_project)
    proj_root = proj_path.parent.resolve().absolute()  # noqa: F841 TODO

    if proj_path.suffix.lower() not in (".tsproj",):
        raise ValueError("Expected a .tsproj file")

    project = pytmc.parser.parse(proj_path)
    results = {}
    success = True
    for i, plc in enumerate(project.plcs, 1):
        source_items = (
            list(plc.dut_by_name.items())
            + list(plc.gvl_by_name.items())
            + list(plc.pou_by_name.items())
        )
        for name, source_item in source_items:
            if not hasattr(source_item, "get_source_code"):
                continue

            source_code = source_item.get_source_code()
            if not source_code:
                continue

            try:
                yield source_item.filename, parse_source_code(
                    source_code, fn=source_item.filename, verbose=verbose
                )
            except Exception as ex:
                tb = traceback.format_exc()
                ex.traceback = tb
                yield source_item.filename, ex

    return success, results


def parse(path: AnyPath) -> Generator[Tuple[pathlib.Path, Any], None, None]:
    """
    Parse the given source code file (or all files from the given project).
    """
    path = pathlib.Path(path)
    filenames = []
    if path.suffix.lower() in (".tsproj",):
        filenames = [path]
    elif path.suffix.lower() in (".sln",):
        filenames = pytmc.parser.projects_from_solution(path)
    else:
        # elif path.suffix.lower() in (".tcpou", ".tcgvl", ".tcdut"):
        filenames = [path]

    for fn in filenames:
        try:
            if fn.suffix.lower() in (".tsproj", ):
                yield from parse_project(fn)
            else:
                yield fn, parse_single_file(fn)
        except Exception as ex:
            tb = traceback.format_exc()
            ex.traceback = tb
            yield fn, ex


def build_arg_parser(argparser=None):
    if argparser is None:
        argparser = argparse.ArgumentParser()

    argparser.description = DESCRIPTION
    argparser.formatter_class = argparse.RawTextHelpFormatter

    argparser.add_argument(
        "filename",
        type=str,
        help=(
            "Path to project, solution, source code file (.tsproj, .sln, "
            ".TcPOU, .TcGVL)"
        ),
    )

    argparser.add_argument(
        "--verbose",
        "-v",
        action="count",
        default=0,
        help="Increase verbosity, up to -vvv",
    )

    argparser.add_argument(
        "--debug", action="store_true",
        help="On failure, still return the results tree"
    )

    argparser.add_argument(
        "-i", "--interactive", action="store_true",
        help="Enter IPython (or Python) to explore source trees"
    )

    argparser.add_argument(
        "-s", "--summary", action="store_true",
        help="Summarize code inputs and outputs"
    )

    return argparser


def summarize(code: tf.SourceCode) -> summary.CodeSummary:
    return summary.CodeSummary.from_source(code)


def main(
    filename: Union[str, pathlib.Path],
    verbose: int = 0,
    debug: bool = False,
    interactive: bool = False,
    summary: bool = False,
):
    """
    Parse the given source code/project.
    """
    result_by_filename = {}
    failures = []
    print_filenames = sys.stdout if verbose > 0 else None
    filename = pathlib.Path(filename)

    for fn, result in parse(filename):
        if print_filenames:
            print(f"* Loading {fn}")
        result_by_filename[fn] = result
        if isinstance(result, Exception):
            failures.append((fn, result))
            if interactive:
                python_debug_session(
                    namespace={"fn": fn, "result": result},
                    message=(
                        f"Failed to parse {fn}. {type(result).__name__}: {result}\n"
                        f"{result.traceback}"
                    ),
                )
            elif verbose > 1:
                print(result.traceback)
        else:
            if summary:
                print(summarize(result_by_filename[fn]))

    if not result_by_filename:
        return {}

    if interactive:
        if len(result_by_filename) > 1:
            python_debug_session(
                namespace={"fn": filename, "results": result_by_filename},
                message=(
                    "Parsed all files successfully: {list(result_by_filename)}\n"
                    "Access all results by filename in the variable ``results``"
                )
            )
        else:
            ((filename, result),) = list(result_by_filename.items())
            python_debug_session(
                namespace={"fn": filename, "result": result},
                message=(
                    f"Parsed single file successfully: {filename}.\n"
                    f"Access its transformed value in the variable ``result``."
                )
            )

    if failures:
        print("Failed to parse some source code files:")
        for fn, exception in failures:
            header = f"{fn}"
            print(header)
            print("-" * len(header))
            print(f"({type(exception).__name__}) {exception}")
            print()
            # if verbose > 1:
            traceback.print_exc()

        if not debug:
            sys.exit(1)

    return result_by_filename
