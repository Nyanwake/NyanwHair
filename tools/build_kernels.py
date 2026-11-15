"""Build the optional C99 kernels with a native compiler or Zig cross-compiler."""
import argparse,os,subprocess,tempfile
from pathlib import Path

parser=argparse.ArgumentParser()
parser.add_argument('--target',choices=('linux-x64','windows-x64'),default='windows-x64' if os.name=='nt' else 'linux-x64')
parser.add_argument('--compiler',default='cl' if os.name=='nt' else 'cc')
parser.add_argument('--zig',help='Path to Zig for cross-compilation')
args=parser.parse_args();root=Path(__file__).resolve().parents[1];source=root/'kernels.c'
output=root/('_kernels_windows_x64.dll' if args.target=='windows-x64' else '_kernels_linux_x64.so')
with tempfile.TemporaryDirectory(prefix='nyanwhair-build-') as directory:
 if args.zig:
  target='x86_64-windows-gnu' if args.target=='windows-x64' else 'x86_64-linux-gnu'
  command=[args.zig,'cc','-target',target,'-std=c99','-shared','-O3','-ffp-contract=off','-fno-fast-math','-g0','-s',str(source),'-o',str(output)]
  if args.target=='linux-x64':command.extend(['-fPIC','-lm'])
 elif Path(args.compiler).name.lower() in ('cl','cl.exe'):
  command=[args.compiler,'/nologo','/std:c11','/LD','/O2','/fp:strict',str(source),'/link','/OUT:'+str(output),'/IMPLIB:'+str(Path(directory)/'kernel.lib')]
 else:
  if args.target=='windows-x64' and os.name!='nt':parser.error('Use --zig for Windows cross-compilation')
  command=[args.compiler,'-std=c99','-shared','-O3','-ffp-contract=off','-fno-fast-math','-g0','-s',str(source),'-o',str(output)]
  if args.target=='linux-x64':command.extend(['-fPIC','-lm'])
 subprocess.run(command,cwd=directory,check=True)
print(output)
