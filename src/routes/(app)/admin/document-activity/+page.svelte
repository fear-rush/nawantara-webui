<script>
  import { onMount } from 'svelte';
  import { mountDashboard } from './dashboard.js';
  import './style.css';

  let root;
  onMount(() => mountDashboard(root));
</script>

<div class="document-activity" bind:this={root}>
  <main>
    <div class="heading"><h1>Document Activity</h1><div class="heading-actions"><a id="rms-link" href="/admin/document-activity" hidden target="_blank" rel="noopener noreferrer">RMS Tasks</a><button id="refresh" type="button">Refresh</button></div></div>
    <p id="health" class="muted" role="status">Connecting to the document worker...</p>
    <div class="tabs" aria-label="Activity views">
      <button id="jobs-tab" type="button" aria-pressed="true">Jobs</button>
      <button id="events-tab" type="button" aria-pressed="false">Document changes</button>
    </div>
    <form id="filters">
      <label>User<select name="actor_id"><option value="">All users and system</option></select></label>
      <label>Status<select name="status"><option value="">All statuses</option><option>queued</option><option>processing</option><option>completed</option><option>failed</option><option>cancelled</option><option>interrupted</option><option>accepted</option></select></label>
      <label>Action<select name="action"><option value="">All actions</option></select></label>
      <label>Source<select name="origin"><option value="">All sources</option><option value="nawantara">Nawantara</option><option value="rms">RMS</option></select></label>
      <label>RMS document ID<input name="document_id" inputmode="numeric" placeholder="Any document"></label>
      <label>From<input name="from" type="date"></label><label>To<input name="to" type="date"></label>
      <button type="submit">Apply filters</button><button id="reset" type="button">Clear</button>
    </form>
    <p id="error" role="alert" hidden></p>
    <div class="table-wrap"><table><caption id="caption" class="sr-only">Document jobs</caption><thead><tr><th scope="col">Action / document</th><th scope="col">User</th><th scope="col">Status</th><th scope="col">Stage</th><th scope="col">Started / recorded</th><th scope="col">Duration</th><th scope="col">Details</th></tr></thead><tbody id="rows"></tbody></table></div>
    <div class="pagination"><span id="count" role="status"></span><div><button id="previous" type="button">Previous</button><button id="next" type="button">Next</button></div></div>
    <p class="muted">Updates every 10 seconds. Private document contents and chat messages are excluded.</p>
  </main>
  <dialog id="detail" aria-labelledby="detail-title">
    <div class="heading"><h2 id="detail-title">Job details</h2><button id="close" type="button">Close</button></div>
    <div id="detail-body"></div><div id="controls" class="controls"></div>
    <p id="detail-error" role="alert" hidden></p>
  </dialog>

</div>
