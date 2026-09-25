#!/bin/sh
# Rebuild the annotated disassembly + call graph in ./work (needs: brew install binutils)
set -e
cd "$(dirname "$0")"; mkdir -p ../work; cd ../work
tail -c +$((0x4080+1)) ../dp2x102.bin > img.bin
/opt/homebrew/opt/binutils/bin/objdump -D -b binary -m fr30 -EB --adjust-vma=0x40000 img.bin > dis.txt
python3 ../tools/annot.py      # -> dis_ann.txt (string refs annotated)
python3 ../tools/cg.py         # -> cg.pkl
python3 ../tools/cg2.py        # -> cg2.pkl (call graph incl. delay-slot calls)
echo "done. e.g.: python3 ../tools/lin.py 38a380 38a4a0 ; python3 ../tools/who.py 2389f0"
