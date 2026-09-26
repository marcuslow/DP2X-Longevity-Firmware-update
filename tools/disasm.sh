#!/bin/sh
# Rebuild the annotated disassembly + call graph (needs: brew install binutils)
#   tools/disasm.sh                 -> work/     from dp2x102.bin (DP2x 1.02)
#   tools/disasm.sh FIRMWARE OUTDIR -> OUTDIR/   from any DP-series firmware file (e.g. 'sigma DP2/dp2v105.bin' work-dp2)
set -e
root="$(cd "$(dirname "$0")/.." && pwd)"
fw="$root/${1:-dp2x102.bin}"; out="$root/${2:-work}"
mkdir -p "$out"; cd "$out"
tail -c +$((0x4080+1)) "$fw" > img.bin
/opt/homebrew/opt/binutils/bin/objdump -D -b binary -m fr30 -EB --adjust-vma=0x40000 img.bin > dis.txt
python3 "$root/tools/annot.py"      # -> dis_ann.txt (string refs annotated)
python3 "$root/tools/cg.py"         # -> cg.pkl
python3 "$root/tools/cg2.py"        # -> cg2.pkl (call graph incl. delay-slot calls)
echo "done. e.g.: python3 $root/tools/lin.py 38a380 38a4a0 ; python3 $root/tools/who.py 2389f0"
