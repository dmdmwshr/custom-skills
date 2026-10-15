function Resolve-VideoNoteModel {
    param($Config, [string]$ExplicitModel)
    if (-not [string]::IsNullOrWhiteSpace($ExplicitModel)) { return $ExplicitModel.Trim() }
    $request = $Config.default_request
    $role = if ($request.PSObject.Properties['model_role']) { [string]$request.model_role } else { '' }
    $rolesPath = if ($Config.PSObject.Properties['model_roles_file']) { [string]$Config.model_roles_file } else { '' }
    if ($role -and $rolesPath) {
        if ($role -notin @('strong','light')) { throw '中央模型角色必须是 strong 或 light。' }
        $rolesPath = [Environment]::ExpandEnvironmentVariables($rolesPath)
        if (Test-Path -LiteralPath $rolesPath -PathType Leaf) {
            if ((Get-Item -LiteralPath $rolesPath).Length -gt 4096) { throw '中央模型配置超出预期大小。' }
            $roles = Get-Content -LiteralPath $rolesPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $entry = $roles.PSObject.Properties[$role]
            if ($null -eq $entry) { throw "中央模型配置缺少角色：$role" }
            $value = $entry.Value
            $model = if ($value -is [string]) { $value } else { '' }
            if ($model -notmatch '^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$' -or $model -in @('strong','light')) { throw "中央模型角色缺少有效型号：$role" }
            return $model.Trim()
        }
    }
    return [string]$request.model_name
}
