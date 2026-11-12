    .text
    .global start, exported
    .def start
    .ref external
start .asmfunc
    ldi r1, 1
exported:
    halt
    .endasmfunc
