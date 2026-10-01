sudo docker ps | grep toolmgmt | awk '{print $1}' | xargs -r sudo docker stop
sudo docker ps -a --filter "name=toolmgmt" -q | xargs -r sudo docker rm -f
sudo docker image ls | grep 'toolmgmt' | awk '{print $3}' | xargs -r sudo docker image rm -f
cd /u02/VC/toolmgmt
sudo docker build -t toolmgmt:latest .
sudo docker run -d --name toolmgmt --restart unless-stopped -p 5000:5000 --env-file .env \
  -e PORT=5000 -e FLASK_DEBUG=0 -e TOOLMGMT_DB_PATH=/u02/VC/toolmgmt/database.db \
  -e TOOLMGMT_SECRET_KEY=replace-with-strong-random-value \
  -e TOOLMGMT_DEFAULT_USERNAME=admin -e TOOLMGMT_DEFAULT_PASSWORD=Admin@1 \
  -e ORACLE_USER=sim_admin -e ORACLE_HOST=192.1.2.45 -e ORACLE_PORT=1521 -e ORACLE_SERVICE=ptsdbin \
  -v /u02/VC/toolmgmt:/u02/VC/toolmgmt toolmgmt:latest
sudo docker ps -a |grep toolmgmt

  
In PowerShell:

Then your prompt shows (.venv) and python/pip resolve to the venv copies. Run the app with:


To deactivate later: deactivate

Note: If you get an execution policy error (scripts disabled), run this once (per user, no admin needed):

Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned

How to know what are all installed in venv?


After activating the venv, list installed packages:
pip list

Or without activating, using the venv pip directly:
D:\Python\.venv\Scripts\pip.exe list

To compare against requirements.txt or export the full list:
D:\Python\.venv\Scripts\pip.exe freeze
