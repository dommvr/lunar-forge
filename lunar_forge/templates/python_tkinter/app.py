"""A small standard-library Tkinter calculator."""

from __future__ import annotations

import operator
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk


OPERATIONS: dict[str, Callable[[float, float], float]] = {
    "+": operator.add,
    "-": operator.sub,
    "×": operator.mul,
    "÷": operator.truediv,
}


class Calculator(ttk.Frame):
    def __init__(self, parent: tk.Tk) -> None:
        super().__init__(parent, padding=24)
        self.first_value = tk.StringVar()
        self.second_value = tk.StringVar()
        self.operation = tk.StringVar(value="+")
        self.result = tk.StringVar(value="Enter two numbers.")
        self._build_ui()

    def _build_ui(self) -> None:
        self.grid(sticky="nsew")
        self.columnconfigure(0, weight=1)

        ttk.Label(self, text="Calculator", font=("TkDefaultFont", 20, "bold")).grid(
            row=0,
            column=0,
            sticky="w",
            pady=(0, 18),
        )
        ttk.Entry(self, textvariable=self.first_value).grid(
            row=1,
            column=0,
            sticky="ew",
            pady=4,
        )
        ttk.Combobox(
            self,
            textvariable=self.operation,
            values=tuple(OPERATIONS),
            state="readonly",
            width=5,
        ).grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(self, textvariable=self.second_value).grid(
            row=3,
            column=0,
            sticky="ew",
            pady=4,
        )
        ttk.Button(self, text="Calculate", command=self.calculate).grid(
            row=4,
            column=0,
            sticky="ew",
            pady=(14, 8),
        )
        ttk.Label(self, textvariable=self.result).grid(row=5, column=0, sticky="w")

    def calculate(self) -> None:
        try:
            first = float(self.first_value.get())
            second = float(self.second_value.get())
            value = OPERATIONS[self.operation.get()](first, second)
        except ValueError:
            self.result.set("Please enter valid numbers.")
            return
        except ZeroDivisionError:
            self.result.set("Division by zero is not allowed.")
            return
        self.result.set(f"Result: {value:g}")


def main() -> None:
    root = tk.Tk()
    root.title("Calculator")
    root.minsize(360, 300)
    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)
    Calculator(root)
    root.mainloop()


if __name__ == "__main__":
    main()
