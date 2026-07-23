# Vite React example

This is a deliberately small React frontend. Generated dependency and build
directories are ignored and are not part of the example.

Run these commands from the LunarForge repository root. The project needs
Node.js and npm, but no secrets, global npm packages, or remote runtime APIs.

## Install

```powershell
cd examples/projects/vite-react
npm install
```

## Develop

```powershell
npm run dev
```

Open the URL printed by Vite and stop the server with `Ctrl+C`.

## Build

```powershell
npm run build
```

## Preview the build

```powershell
npm run preview
```

## Cleanup

```powershell
$Generated = @("node_modules", "dist", "package-lock.json")
$Generated | ForEach-Object {
    Remove-Item -Recurse -Force -LiteralPath $_ -ErrorAction SilentlyContinue
}
```
