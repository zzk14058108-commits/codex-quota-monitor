# Use the existing visible desktop owner. Never open or terminate the APP.
param(
  [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-fA-F-]{36}$')][string]$ThreadId,
  [ValidateSet('Probe','Pause','Resume')][string]$Operation='Probe',
  [string]$MessageFile,[string]$ClientMessageId,[string]$ReceiptFile,
  [string]$GuardFile,[double]$PausedAt=0,
  [int]$TimeoutMs=10000
)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$pipe=[IO.Pipes.NamedPipeClientStream]::new('.','codex-ipc',[IO.Pipes.PipeDirection]::InOut,[IO.Pipes.PipeOptions]::Asynchronous)
$clock=[Diagnostics.Stopwatch]::StartNew();$script:clientId='initializing-client'
$script:deadline=$TimeoutMs
$receipt=@{operation=$Operation;thread_id=$ThreadId;stage='not_sent';app_closed=$false;headless_model_started=$false}
function Save-Receipt {
  if ($ReceiptFile) {
    $temp=$ReceiptFile+'.'+[guid]::NewGuid().ToString()+'.tmp'
    [IO.File]::WriteAllText($temp,($receipt | ConvertTo-Json -Depth 12 -Compress),[Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temp -Destination $ReceiptFile -Force
  }
}
function Send-Frame($value) {
  $body=[Text.Encoding]::UTF8.GetBytes(($value | ConvertTo-Json -Depth 20 -Compress))
  if ($value.method -eq 'thread-follower-start-turn') {
    $receipt.stage='encoded';$receipt.frame_bytes=$body.Length;Save-Receipt
  }
  $prefix=[BitConverter]::GetBytes([uint32]$body.Length)
  foreach ($part in @($prefix,$body)) {
    $job=$pipe.WriteAsync($part,0,$part.Length)
    $left=$script:deadline-[int]$clock.ElapsedMilliseconds
    if ($left -le 0 -or -not $job.Wait($left)) {throw 'Desktop IPC write timeout'}
    $job.GetAwaiter().GetResult()
  }
  if ($value.method -eq 'thread-follower-start-turn') {
    $receipt.stage='sent';$receipt.sent_at=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000.0;Save-Receipt
  }
}
function Read-Exact([int]$length) {
  $buffer=[byte[]]::new($length);$offset=0
  while ($offset -lt $length) {
    $left=$script:deadline-[int]$clock.ElapsedMilliseconds
    if ($left -le 0) {throw 'Desktop IPC deadline'}
    $job=$pipe.ReadAsync($buffer,$offset,$length-$offset)
    if (-not $job.Wait($left)) {throw 'Desktop IPC timeout'}
    $count=$job.GetAwaiter().GetResult();if ($count -le 0) {throw 'Desktop IPC closed'}
    $offset+=$count
  }
  return ,$buffer
}
function Request([string]$method,$params,[int]$version,[string]$target='') {
  $script:deadline=[int]$clock.ElapsedMilliseconds+$TimeoutMs
  $id=[guid]::NewGuid().ToString()
  $value=@{type='request';requestId=$id;sourceClientId=$script:clientId;version=$version;method=$method;params=$params;timeoutMs=($TimeoutMs-500)}
  if ($target) {$value.targetClientId=$target}
  if ($method -eq 'thread-follower-start-turn') {
    # Recheck immediately before sending, after owner discovery. Never use a weekly value as five-hour data.
    $guard=Get-Content -LiteralPath $GuardFile -Raw -Encoding utf8 | ConvertFrom-Json
    $now=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000.0
    $five=@($guard.quota_windows | Where-Object {$_.windowDurationMins -eq 300})
    $week=@($guard.quota_windows | Where-Object {$_.windowDurationMins -eq 10080})
    if ($guard.mode -ne 'ALLOWED' -or $guard.quota_source -ne 'REAL_ACCOUNT' -or $guard.last_read_error -or
        $guard.test_override_active -or $guard.test_timeout_fault_active -or
        $five.Count -ne 1 -or $week.Count -ne 1 -or (100-$five[0].usedPercent) -le 5 -or (100-$week[0].usedPercent) -le 5 -or
        $guard.quota_observed_at -le $PausedAt -or ($now-$guard.quota_observed_at) -gt 20 -or ($now-$guard.heartbeat_at) -gt 5) {
      throw 'No fresh explicit five-hour allowance for this paused task'
    }
    $receipt.stage='preparing';$receipt.request_id=$id;$receipt.client_message_id=$ClientMessageId
    $receipt.quota_observed_at=$guard.quota_observed_at
    $receipt.five_hour_remaining_percent=100-$five[0].usedPercent;$receipt.weekly_remaining_percent=100-$week[0].usedPercent
    $receipt.dispatched_at=$now;Save-Receipt
  }
  Send-Frame $value
  while ($true) {
    $prefix=Read-Exact 4;$size=[BitConverter]::ToUInt32($prefix,0)
    if ($size -le 0 -or $size -gt 8388608) {throw 'Invalid IPC frame size'}
    $bytes=Read-Exact ([int]$size);$reply=[Text.Encoding]::UTF8.GetString($bytes) | ConvertFrom-Json
    if ($reply.type -eq 'client-discovery-request') {
      Send-Frame @{type='client-discovery-response';requestId=$reply.requestId;response=@{canHandle=$false}}
    } elseif ($reply.type -eq 'request') {
      Send-Frame @{type='response';requestId=$reply.requestId;resultType='error';error='no-handler-for-request'}
    } elseif ($reply.type -eq 'response' -and $reply.requestId -eq $id) {
      if ($reply.resultType -ne 'success') {
        if ($method -eq 'thread-follower-start-turn') {$receipt.stage='rejected';$receipt.error=[string]$reply.error;Save-Receipt}
        throw "$method : $($reply.error)"
      }
      return $reply
    }
  }
}
try {
  Save-Receipt
  $pipe.Connect(1000)
  $init=Request 'initialize' @{clientType='codex-quota-interrupt-only'} 0
  $script:clientId=[string]$init.result.clientId
  $owner=Request 'thread-owner-discovery' @{hostId='local';conversationId=$ThreadId} 1
  $ownerId=[string]$owner.handledByClientId
  if (-not $ownerId) {throw 'No current visible desktop owner'}
  $record=@{operation=$Operation;thread_id=$ThreadId;owner_client_id=$ownerId;app_closed=$false;headless_model_started=$false}
  if ($Operation -eq 'Probe') {
    $reply=Request 'thread-follower-read-model-settings' @{conversationId=$ThreadId} 1 $ownerId
    $record.model=$reply.result.settings.model;$record.reasoning_effort=$reply.result.settings.reasoningEffort
  } elseif ($Operation -eq 'Pause') {
    # user-stop pauses goal/queued continuation and cleans this thread's tools.
    $reply=Request 'thread-follower-interrupt-turn' @{conversationId=$ThreadId;mode='user-stop'} 3 $ownerId
    if ($reply.result.ok -ne $true) {throw 'Desktop did not acknowledge interruption'}
    $record.interrupted_turn_id=$reply.result.interruptedTurnId
    $record.goal_pause_error=$reply.result.goalPauseError
    $record.interrupt_ack=$true
  } else {
    if (-not $MessageFile -or -not $ClientMessageId -or -not $ReceiptFile -or -not $GuardFile) {throw 'Resume requires message, identity, receipt and live quota guard'}
    # Windows PowerShell 5 decorates Get-Content strings with filesystem/provider
    # properties. ConvertTo-Json -Depth20 recursively expands that graph instead
    # of emitting a plain text value. Read a plain .NET string directly.
    $text=[IO.File]::ReadAllText($MessageFile,[Text.Encoding]::UTF8)
    # Match the installed desktop's native input schema: text_elements, not textElements.
    $turn=@{request=@{threadId=$ThreadId;clientUserMessageId=$ClientMessageId;input=@(@{type='text';text=$text;text_elements=@()})};context=@{inheritThreadSettings=$true}}
    $reply=Request 'thread-follower-start-turn' @{conversationId=$ThreadId;turnStart=$turn} 2 $ownerId
    $turnId=[string]$reply.result.result.turn.id
    if (-not $turnId) {throw 'Resume response missing accepted turn ID'}
    $record.resume_ack=$true;$record.client_message_id=$ClientMessageId;$record.resumed_turn_id=$turnId
    $receipt.stage='accepted';$receipt.resume_ack=$true;$receipt.resumed_turn_id=$turnId
    $receipt.acknowledged_at=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000.0;Save-Receipt
  }
  $record | ConvertTo-Json -Depth 8 -Compress
} catch {
  $receipt.error=$_.Exception.Message;Save-Receipt
  throw
} finally {$pipe.Dispose()}
