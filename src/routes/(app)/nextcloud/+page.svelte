<script lang="ts">
	import { onMount } from 'svelte';
	import { nextcloud } from '$lib/apis/nextcloud';
	let connection = { connected: false, username: '', scopes: [] as string[], url: '' };
	let username = '';
	let password = '';
	let scopes = '/Documents';
	let folder = '/';
	let query = '';
	let destination = '/Documents/findings.md';
	let text = '';
	let error = '';
	let message = '';
	let busy = false;
	let files: { file_id: string; path: string; folder: boolean; url: string }[] = [];
	let sources: { file_id: string; path: string; url: string; text: string }[] = [];
	let jobs: { id: string; action: string; status: string; stage: string; error: string; result: { url?: string; index_job_id?: string } }[] = [];
	const controller = new AbortController();
	async function refresh() {
		connection = await nextcloud('/connection', 'GET', undefined, controller.signal);
		if (connection.connected) jobs = (await nextcloud('/jobs', 'GET', undefined, controller.signal)).jobs;
	}
	async function action(work: () => Promise<void>) {
		busy = true; error = ''; message = '';
		try { await work(); await refresh(); }
		catch (e) { if (!controller.signal.aborted) error = e instanceof Error ? e.message : 'Request failed'; }
		finally { busy = false; }
	}
	onMount(() => {
		action(refresh);
		const timer = setInterval(() => { if (!busy) action(refresh); }, 10000);
		return () => { clearInterval(timer); controller.abort(); };
	});
	async function connect() {
		const secret = password; password = '';
		await nextcloud('/connection', 'POST', { username, app_password: secret, scopes: scopes.split('\n').map(s => s.trim()).filter(Boolean) }, controller.signal);
		message = 'Connected. Indexing is queued.';
	}
	async function browse() { files = (await nextcloud(`/files?path=${encodeURIComponent(folder)}`, 'GET', undefined, controller.signal)).files; }
	async function search() { sources = (await nextcloud(`/search?query=${encodeURIComponent(query)}`, 'GET', undefined, controller.signal)).sources; }
	async function save() {
		const result = await nextcloud('/save', 'POST', { path: destination, text }, controller.signal);
		message = `Save queued: ${result.job_id}`;
	}
</script>

<svelte:head><title>Nextcloud | Nawantara</title></svelte:head>
<main class="mx-auto w-full max-w-4xl overflow-y-auto p-6 space-y-6">
	<div class="flex items-center justify-between gap-4"><h1 class="text-xl font-semibold">Nextcloud</h1>
		{#if connection.url}<a class="underline" href={connection.url} target="_blank" rel="noopener noreferrer">Open Nextcloud</a>{/if}
	</div>
	{#if error}<p role="alert" class="text-red-600 dark:text-red-400">{error}</p>{/if}
	{#if message}<p role="status">{message}</p>{/if}
	<section class="space-y-3">
		<h2 class="font-semibold">Account connection</h2>
		{#if connection.connected}
			<p>Connected as {connection.username}. Indexed folders: {connection.scopes.join(', ')}</p>
			<div class="flex gap-3">
				<button class="rounded-lg border px-3 py-2" disabled={busy} on:click={() => action(async () => { await nextcloud('/index', 'POST', {}, controller.signal); message = 'Indexing queued.'; })}>Refresh index</button>
				<button class="rounded-lg border px-3 py-2" disabled={busy} on:click={() => action(async () => { await nextcloud('/connection', 'DELETE', undefined, controller.signal); files = []; sources = []; jobs = []; })}>Disconnect and remove index</button>
			</div>
		{:else}
			<p>Create an app password in Nextcloud under Personal settings, Security. Choose existing folders to index. Your Nextcloud permissions apply to every read.</p>
			<form class="space-y-3" on:submit|preventDefault={() => action(connect)}>
				<label class="block">Nextcloud username<input class="mt-1 block w-full rounded-lg border bg-transparent p-2" bind:value={username} required autocomplete="username" /></label>
				<label class="block">App password<input class="mt-1 block w-full rounded-lg border bg-transparent p-2" type="password" bind:value={password} required autocomplete="new-password" /></label>
				<label class="block">Indexing folders, one per line<textarea class="mt-1 block w-full rounded-lg border bg-transparent p-2" bind:value={scopes} required rows="3"></textarea></label>
				<button class="rounded-lg border px-3 py-2" disabled={busy}>Connect</button>
			</form>
		{/if}
	</section>
	{#if connection.connected}
		<section class="space-y-3">
			<h2 class="font-semibold">Files</h2>
			<form class="flex gap-2" on:submit|preventDefault={() => action(browse)}>
				<label class="grow">Folder<input class="block w-full rounded-lg border bg-transparent p-2" bind:value={folder} required /></label>
				<button class="self-end rounded-lg border px-3 py-2" disabled={busy}>Browse</button>
			</form>
			<ul>{#each files as file}<li class="py-1">{#if file.folder}<button class="underline" on:click={() => action(async () => { folder = file.path; await browse(); })}>{file.path}/</button>{:else}<a class="underline" href={file.url} target="_blank" rel="noopener noreferrer">{file.path}</a>{/if}</li>{/each}</ul>
		</section>
		<section class="space-y-3">
			<h2 class="font-semibold">Search indexed content</h2>
			<form class="flex gap-2" on:submit|preventDefault={() => action(search)}>
				<label class="grow">Query<input class="block w-full rounded-lg border bg-transparent p-2" bind:value={query} required /></label>
				<button class="self-end rounded-lg border px-3 py-2" disabled={busy}>Search</button>
			</form>
			{#each sources as source}<article class="rounded-lg border p-3 space-y-2"><a class="underline" href={source.url} target="_blank" rel="noopener noreferrer">{source.path}</a><p class="whitespace-pre-wrap">{source.text}</p><button class="underline" on:click={() => action(async () => { await nextcloud(`/index/${encodeURIComponent(source.file_id)}`, 'DELETE', undefined, controller.signal); sources = sources.filter(s => s.file_id !== source.file_id); message = 'Removed from this index. The original remains in Nextcloud.'; })}>Exclude from my index</button></article>{/each}
		</section>
		<section class="space-y-3">
			<h2 class="font-semibold">Save findings</h2>
			<p>The new file inherits its destination folder's sharing. Existing files are never overwritten.</p>
			<form class="space-y-3" on:submit|preventDefault={() => action(save)}>
				<label class="block">Destination file<input class="mt-1 block w-full rounded-lg border bg-transparent p-2" bind:value={destination} required /></label>
				<label class="block">Content<textarea class="mt-1 block w-full rounded-lg border bg-transparent p-2" bind:value={text} required rows="5"></textarea></label>
				<button class="rounded-lg border px-3 py-2" disabled={busy}>Save to Nextcloud</button>
			</form>
		</section>
		<section class="space-y-3"><h2 class="font-semibold">My processing jobs</h2>
			{#each jobs as job}<article class="rounded-lg border p-3 space-y-2"><p>{job.action}: {job.status} ({job.stage})</p><p class="text-xs">{job.id}</p>{#if job.error}<p>{job.error}</p>{/if}{#if job.result.url}<a class="underline" href={job.result.url} target="_blank" rel="noopener noreferrer">Open saved file</a>{/if}
			{#if ['queued','processing'].includes(job.status)}<button class="ml-3 underline" on:click={() => action(async () => { await nextcloud(`/jobs/${job.id}`, 'POST', {operation:'cancel'}, controller.signal); })}>Cancel remaining work</button>{/if}
			{#if ['failed','interrupted','cancelled'].includes(job.status)}<button class="ml-3 underline" on:click={() => action(async () => { await nextcloud(`/jobs/${job.id}`, 'POST', {operation:'retry'}, controller.signal); })}>Retry</button>{/if}
			</article>{/each}
		</section>
	{/if}
</main>
