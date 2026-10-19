param([Parameter(Mandatory=$true)][string]$FleetHome, [string]$RenderTest, [ValidateSet('Decisions','Fleet Status')][string]$RenderTab = 'Decisions', [switch]$RenderMinimum)
# Keep UTF-8 BOM: the installed Windows PowerShell 5.1 otherwise decodes Chinese as ANSI.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$FleetHome = (Resolve-Path -LiteralPath $FleetHome).Path
$snapshotPath = Join-Path $FleetHome 'panel.json'
$inbox = Join-Path $FleetHome 'inbox'
if (-not (Test-Path -LiteralPath $snapshotPath) -or -not (Test-Path -LiteralPath $inbox)) { throw '请先启用 StarFix，再打开面板' }
# Translate presentation only. Persisted status codes and user-authored text stay unchanged.
function Format-PanelState($value) {
    $labels = @{ pending='待处理'; idle='空闲'; busy='执行中'; retry='重试中'; unknown='未确认';
        running='运行中'; paused='已暂停'; done='已完成'; completed='已完成'; blocked='受阻';
        failed='失败'; error='错误'; healthy='正常'; degraded='异常'; repairing='修复中';
        stopped='已停止'; disabled='未启用'; go='可用'; stop='停止'; pass='通过'; fail='未通过';
        unavailable='不可用'; unconfirmed='待确认'; confirmed='已确认'; creation_unconfirmed='创建待确认' }
    if ($null -eq $value -or [string]::IsNullOrWhiteSpace([string]$value)) { return '暂无数据' }
    if ($labels.ContainsKey([string]$value)) { return $labels[[string]$value] }
    return [string]$value
}
[System.Windows.Forms.Application]::EnableVisualStyles()
$form = New-Object System.Windows.Forms.Form
$form.Text = 'StarFix 舰队面板'
$form.Size = New-Object System.Drawing.Size(840,650)
$form.MinimumSize = New-Object System.Drawing.Size(680,520)
$form.StartPosition = 'CenterScreen'
$form.Font = New-Object System.Drawing.Font('Segoe UI',10)
$tabs = New-Object System.Windows.Forms.TabControl
$tabs.Dock = 'Fill'
$questionsTab = New-Object System.Windows.Forms.TabPage
$questionsTab.Text = '待确认事项'
$statusTab = New-Object System.Windows.Forms.TabPage
$statusTab.Text = '舰队状态'
$tabs.Controls.AddRange(@($questionsTab,$statusTab))
$form.Controls.Add($tabs)
$fleetStatus = New-Object System.Windows.Forms.TextBox
$fleetStatus.Dock = 'Fill'
$fleetStatus.Multiline = $true
$fleetStatus.ReadOnly = $true
$fleetStatus.ScrollBars = 'Both'
$fleetStatus.WordWrap = $false
$statusTab.Controls.Add($fleetStatus)
$layout = New-Object System.Windows.Forms.TableLayoutPanel
$layout.Dock = 'Fill'
$layout.ColumnCount = 1
$layout.RowCount = 5
@(44,160,180,80,48) | ForEach-Object { $style = New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Percent,$_); [void]$layout.RowStyles.Add($style) }
$questionsTab.Controls.Add($layout)
$status = New-Object System.Windows.Forms.Label
$status.Dock = 'Fill'
$status.AutoEllipsis = $true
$list = New-Object System.Windows.Forms.ListBox
$list.Dock = 'Fill'
$list.DisplayMember = 'label'
$detail = New-Object System.Windows.Forms.TextBox
$detail.Dock = 'Fill'
$detail.Multiline = $true
$detail.ReadOnly = $true
$detail.ScrollBars = 'Vertical'
$answer = New-Object System.Windows.Forms.TextBox
$answer.Dock = 'Fill'
$answer.Multiline = $true
$answer.MaxLength = 0
$buttons = New-Object System.Windows.Forms.FlowLayoutPanel
$buttons.Dock = 'Fill'
$submit = New-Object System.Windows.Forms.Button
$submit.Text = '提交答复'
$submit.AutoSize = $true
$pin = New-Object System.Windows.Forms.CheckBox
$pin.Text = '窗口置顶'
$pin.AutoSize = $true
$pin.Add_CheckedChanged({ $form.TopMost = $pin.Checked })
$buttons.Controls.AddRange(@($submit,$pin))
$layout.Controls.Add($status,0,0)
$layout.Controls.Add($list,0,1)
$layout.Controls.Add($detail,0,2)
$layout.Controls.Add($answer,0,3)
$layout.Controls.Add($buttons,0,4)
$script:view = $null
$script:lastText = ''
$script:shown = $null
$list.Add_SelectedIndexChanged({
    $script:shown = $list.SelectedItem
    if ($null -ne $script:shown) {
        $q = $script:shown.question
        $detail.Text = "$($q.qid)  $($q.who)`r`n$($q.question)`r`n`r`n$($q.detail)`r`n`r`n建议：$($q.recommend)`r`n关联任务：$($q.tasks -join ', ')"
    }
})
$refresh = {
    try {
        $text = [IO.File]::ReadAllText($snapshotPath,[Text.Encoding]::UTF8)
        $v = $text | ConvertFrom-Json
        $script:view = $v
        $pausedText = if ($v.paused) { '是' } else { '否' }
        $summary = @("项目：$($v.project)", "舰长：$($v.captain)", "已暂停：$pausedText $($v.reason)", '', '任务')
        $summary += @($v.tasks | ForEach-Object { "$($_.id) | $(Format-PanelState $_.status) | $($_.owner) | $($_.title) | $($_.blocker)" })
        $summary += @('', '舰员')
        $summary += @($v.workers | ForEach-Object { "$($_.name) | $(Format-PanelState $_.status) | $($_.directory)" })
        $summary += @('', '未读事件')
        $summary += @($v.events | ForEach-Object { "$($_.at) | $($_.type) | $($_.subject)" })
        $summary += @('', '执行轨迹', "$(Format-PanelState $v.trajectory.overall) | $($v.trajectory.at)", "$($v.trajectory.run)")
        $summary += @('', '账户额度', "$(Format-PanelState $v.quota.status) | 已用：$($v.quota.usedPercent)% | 额度类别：$($v.quota.limitId) | $($v.quota.observedAt)")
        $summary += @('', '通信通道')
        $summary += @($v.channels.PSObject.Properties | ForEach-Object { "$($_.Name) | $(Format-PanelState $_.Value.state) | 失败次数：$($_.Value.failures)" })
        $summary += @('', '原生审计', "$(Format-PanelState $v.audit.state) | $($v.audit.checkedAt)")
        if ($null -ne $v.auditSnapshot) { $summary += ($v.auditSnapshot | ConvertTo-Json -Depth 12) }
        $fleetStatus.Text = $summary -join "`r`n"
        $age = ([DateTimeOffset]::UtcNow - [DateTimeOffset]::Parse($v.updated)).TotalSeconds
        $status.Text = "$($v.project)`r`n已暂停：$pausedText | 任务：$(@($v.tasks).Count) | 舰员：$(@($v.workers).Count) | 未读事件：$(@($v.events).Count) | 快照更新于 $([int]$age) 秒前 $($v.reason)"
        $submit.Enabled = $true
        $key = $v.captain + (@($v.decisions | ForEach-Object { "$($_.qid):$($_.questionHash)" }) -join ',')
        if ($key -ne $script:lastText) {
            $selected = if ($list.SelectedItem) { $list.SelectedItem.question.qid } else { '' }
            $list.Items.Clear()
            $detail.Clear()
            foreach ($q in $v.decisions) {
                $item = [PSCustomObject]@{label="$($q.qid)  $($q.question)";question=$q;captain=$v.captain}
                $index = $list.Items.Add($item)
                if ($q.qid -eq $selected) { $list.SelectedIndex = $index }
            }
            if ($list.SelectedIndex -lt 0 -and $list.Items.Count -gt 0) { $list.SelectedIndex = 0 }
            $script:lastText = $key
        }
    } catch { $status.Text = '暂时无法读取快照，不能提交答复。'; $submit.Enabled = $false }
}
$submit.Add_Click({
    if ($null -eq $script:shown -or [string]::IsNullOrWhiteSpace($answer.Text)) { return }
    $q = $script:shown.question
    # Explicit user gesture and confirmation. The consumer revalidates the exact
    # question fingerprint and captain before applying an answer to original state.
    $confirm = [Windows.Forms.MessageBox]::Show("$($q.qid): $($q.question)`r`n`r`n$($answer.Text)",'确认提交答复','OKCancel','Question')
    if ($confirm -ne [Windows.Forms.DialogResult]::OK) { return }
    $id = [Guid]::NewGuid().ToString()
    $record = @{id=$id;captain=$script:shown.captain;qid=$q.qid;questionHash=$q.questionHash;answer=$answer.Text;at=[DateTimeOffset]::UtcNow.ToString('o')}
    $target = Join-Path $inbox "$id.json"
    $temp = Join-Path $inbox "$id.pending"
    try {
        [IO.File]::WriteAllText($temp,($record | ConvertTo-Json -Depth 5),(New-Object Text.UTF8Encoding($false)))
        [IO.File]::Move($temp,$target)
        $answer.Clear()
        $status.Text = '答复已排队，处理结果将显示在 StarFix 事件中。'
    } catch { [void][Windows.Forms.MessageBox]::Show('尚未确认答复是否提交成功，请先检查收件箱，不要直接重复提交。') }
})
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 3000
$timer.Add_Tick($refresh)
$form.Add_Shown({ & $refresh; $timer.Start() })
$form.Add_FormClosed({ $timer.Stop(); $timer.Dispose() })
if ($RenderTest) {
    [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
    & $refresh
    if ($RenderMinimum) { $form.Size = $form.MinimumSize }
    if ($RenderTab -eq 'Fleet Status') { $tabs.SelectedTab = $statusTab }
    $form.ShowInTaskbar = $false
    $form.Opacity = 0
    $form.Show()
    $form.PerformLayout()
    [Windows.Forms.Application]::DoEvents()
    $bitmap = New-Object Drawing.Bitmap($form.Width,$form.Height)
    try {
        $form.DrawToBitmap($bitmap,(New-Object Drawing.Rectangle(0,0,$form.Width,$form.Height)))
        $bitmap.Save($RenderTest,[Drawing.Imaging.ImageFormat]::Png)
        @{title=$form.Text; tabs=@($questionsTab.Text,$statusTab.Text); status=$status.Text; detail=$detail.Text; fleetStatus=$fleetStatus.Text; submit=$submit.Text; pin=$pin.Text} | ConvertTo-Json -Compress
    }
    finally { $bitmap.Dispose(); $form.Close() }
} else { [void]$form.ShowDialog() }
$form.Dispose()
