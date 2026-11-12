    .retain
    .retainrefs
    .global start
    .sect ".text:main"
start: .asmfunc
    ldi r1, 1
    halt
    .endasmfunc
