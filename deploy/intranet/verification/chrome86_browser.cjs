// Current authenticated workspace compatibility regression. Set CHROME86_BIN for real Chromium 86.
const {spawnSync}=require('node:child_process');
const {resolve}=require('node:path');
const result=spawnSync('npm',['run','test:browser','--','tests/compatibility.spec.ts'],{
  cwd:resolve(__dirname,'../source/apps/dashboard'),env:process.env,stdio:'inherit'});
process.exit(result.status??1);
