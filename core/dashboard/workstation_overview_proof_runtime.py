from __future__ import annotations

"""Proof and adaptation detail runtime fragment for the workstation dashboard."""

WORKSTATION_OVERVIEW_PROOF_RUNTIME = '''
    function renderProofMiniStats(proof, data) {
      const proofCounters = [
        Number(proof.pending_count || 0),
        Number(proof.confirmed_count || 0),
        Number(proof.finalized_count || 0),
        Number(proof.rejected_count || 0),
        Number(proof.slashed_count || 0),
        Number(proof.finalized_compute_credits || 0),
      ];
      const proofHasLiveData = proofCounters.some((value) => value > 0);
      document.getElementById('proofMiniStats').innerHTML = proofHasLiveData
        ? [
            ['Pending', proof.pending_count || 0],
            ['Confirmed', proof.confirmed_count || 0],
            ['Finalized', proof.finalized_count || 0],
            ['Rejected', proof.rejected_count || 0],
            ['Slashed', proof.slashed_count || 0],
            ['Finalized credits', Number(proof.finalized_compute_credits || 0).toFixed(2)],
          ].map(([label, value]) => `
            <div class="mini-stat">
              <strong>${esc(String(value))}</strong>
              <div>${esc(label)}</div>
            </div>
          `).join('')
        : [
            {
              label: 'Proof counters',
              summary: 'No live finalized/rejected/slashed proof counters are present in the current watcher payload yet.',
            },
            {
              label: 'Receipts',
              summary: 'No live proof receipts are visible yet, so the dashboard says that explicitly instead of showing dead zero theater.',
            },
          ].map((item) => `
            <article class="card" ${inspectAttrs('Observation', item.label, {
              title: item.label,
              summary: item.summary,
              truth_label: 'watcher-derived',
              freshness: 'current',
              status: 'no live data yet',
              source_meet_url: data.source_meet_url || '',
            })}>
              <h3>${esc(item.label)}</h3>
              <p>${esc(item.summary)}</p>
            </article>
          `).join('');
    }

    function renderGloryLeaderList(proof) {
      const leaders = Array.isArray(proof.leaders) ? proof.leaders : [];
      document.getElementById('gloryLeaderList').innerHTML = leaders.length ? leaders.slice(0, 5).map((row) => `
        <article class="card">
          <h3>${esc(shortId(row.peer_id, 18))}</h3>
          <p>${esc(`Glory ${Number(row.glory_score || 0).toFixed(1)} · finality ${(Number(row.finality_ratio || 0) * 100).toFixed(0)}%`)}</p>
          <div class="row-meta">
            ${chip(`F ${fmtNumber(row.finalized_work_count || 0)}`, 'ok')}
            ${chip(`C ${fmtNumber(row.confirmed_work_count || 0)}`)}
            ${chip(`P ${fmtNumber(row.pending_work_count || 0)}`)}
            ${(Number(row.rejected_work_count || 0) + Number(row.slashed_work_count || 0)) > 0 ? chip(`X ${fmtNumber(Number(row.rejected_work_count || 0) + Number(row.slashed_work_count || 0))}`, 'warn') : ''}
            ${chip(row.tier || 'Newcomer')}
          </div>
        </article>
      `).join('') : '<div class="empty">No solver glory yet. Finalized work will appear here after the challenge window clears.</div>';
    }

    function renderProofReceiptList(proof) {
      const receipts = Array.isArray(proof.recent_receipts) ? proof.recent_receipts : [];
      document.getElementById('proofReceiptList').innerHTML = receipts.length ? receipts.slice(0, 5).map((row) => `
        <article class="card">
          <h3>${esc(`Receipt ${shortId(row.receipt_hash || row.receipt_id, 16)}`)}</h3>
          <p>${esc(`Stage ${row.stage || 'unknown'} · task ${shortId(row.task_id || '', 14)} · helper ${shortId(row.helper_peer_id || '', 14)}`)}</p>
          <div class="row-meta">
            ${chip(`depth ${fmtNumber(row.finality_depth || 0)}/${fmtNumber(row.finality_target || 0)}`, row.stage === 'finalized' ? 'ok' : '')}
            ${Number(row.compute_credits || 0) > 0 ? chip(`credits ${Number(row.compute_credits || 0).toFixed(2)}`) : ''}
            ${row.challenge_reason ? chip(compactText(row.challenge_reason, 36), 'warn') : ''}
          </div>
        </article>
      `).join('') : '<div class="empty">No proof receipts yet.</div>';
    }

    function renderAdaptationProofList(adaptationProof) {
      const promotionHistory = Array.isArray(adaptationProof.promotion_history) ? adaptationProof.promotion_history : [];
      document.getElementById('adaptationProofList').innerHTML = [
        `<article class="card"><h3>Model Proof</h3><p>${esc(`State ${adaptationProof.proof_state || 'no_recent_eval'} · mean delta ${Number(adaptationProof.mean_delta || 0).toFixed(3)}`)}</p><div class="row-meta">${chip(`evals ${fmtNumber(adaptationProof.recent_eval_count || 0)}`)}${chip(`positive ${fmtNumber(adaptationProof.positive_eval_count || 0)}`, (adaptationProof.positive_eval_count || 0) > 0 ? 'ok' : '')}${chip(`rollbacks ${fmtNumber(adaptationProof.rolled_back_job_count || 0)}`, (adaptationProof.rolled_back_job_count || 0) > 0 ? 'warn' : '')}</div></article>`,
        ...promotionHistory.slice(0, 3).map((row) => `
          <article class="card">
            <h3>${esc(row.label || row.job_id || 'Adaptation job')}</h3>
            <p>${esc(`${row.adapter_provider_name || 'provider'}:${row.adapter_model_name || 'model'} · quality ${Number(row.quality_score || 0).toFixed(2)}`)}</p>
            <div class="row-meta">
              ${chip(row.status || 'unknown', row.status === 'promoted' ? 'ok' : row.status === 'rolled_back' ? 'warn' : '')}
              ${row.promoted_at ? chip('promoted', 'ok') : ''}
              ${row.rolled_back_at ? chip('rolled_back', 'warn') : ''}
            </div>
          </article>
        `),
      ].join('');
    }
'''
