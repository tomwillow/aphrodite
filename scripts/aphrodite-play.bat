@echo off
rem Aphrodite protocol handler: open the path after aphrodite-play://
rem with the system default player.
set "url=%~1"
set "path=%url:aphrodite-play://=%"
if "%path%"=="" exit /b 1
start "" "%path%"
