<script lang="ts">
	import { onMount } from 'svelte';
	import { nextcloud } from '$lib/apis/nextcloud';
	let jobs: { id: string; user_id: string; action: string; status: string; stage: string; error: string }[] = [];
	let error = '';
	const controller = new AbortController();
	async function refresh() {
		try { jobs = (await nextcloud('/admin/jobs', 'GET', undefined, controller.signal)).jobs; error = ''; }
		catch (e) { if (!controller.signal.aborted) error = e instanceof Error ? e.message : 'Request failed'; }
	}
	onMount(() => { refresh(); const timer = setInterval(refresh, 10000); return () => { clearInterval(timer); controller.abort(); }; });
</script>
<main class="w-full overflow-auto p-6 space-y-4">
	<div class="flex justify-between"><h1 class="text-xl font-semibold">Document Activity</h1><button class="rounded-lg border px-3 py-2" on:click={refresh}>Refresh</button></div>
	<p>Save and indexing status. Private filenames, content and credentials are excluded.</p>
	{#if error}<p role="alert">{error}</p>{/if}
	<table class="w-full text-left"><thead><tr><th class="p-2">Job</th><th class="p-2">User ID</th><th class="p-2">Action</th><th class="p-2">Status</th><th class="p-2">Stage</th></tr></thead>
	<tbody>{#each jobs as job}<tr class="border-t"><td class="p-2 text-xs">{job.id}</td><td class="p-2 text-xs">{job.user_id}</td><td class="p-2">{job.action}</td><td class="p-2">{job.status}</td><td class="p-2">{job.stage}</td></tr>{/each}</tbody></table>
</main>
