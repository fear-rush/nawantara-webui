<script lang="ts">
	import { onMount } from 'svelte';
	import { mobile, showSidebar, WEBUI_NAME } from '$lib/stores';
	import {
		nextcloud,
		type CloudConnection,
		type CloudFile,
		type CloudSource,
		type Job
	} from '$lib/apis/nextcloud';
	import Badge from '$lib/components/common/Badge.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import Sidebar from '$lib/components/icons/Sidebar.svelte';
	import Folder from '$lib/components/icons/Folder.svelte';
	import Document from '$lib/components/icons/Document.svelte';
	import Search from '$lib/components/icons/Search.svelte';
	import ArrowUpRight from '$lib/components/icons/ArrowUpLeft.svelte';
	import JobDetail from '$lib/components/nextcloud/JobDetail.svelte';
	import { workspaceUi as ui, label, badge, date } from '$lib/components/nextcloud/presentation';
	let connection: CloudConnection = { connected: false, url: '' };
	let tab = 'files';
	const tabs = [
		{ id: 'files', label: 'Files' },
		{ id: 'search', label: 'Search' },
		{ id: 'save', label: 'Save findings' },
		{ id: 'activity', label: 'Activity' },
		{ id: 'connection', label: 'Connection' }
	];
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
	let loaded = false;
	let searched = false;
	let browsed = false;
	let files: CloudFile[] = [];
	let sources: CloudSource[] = [];
	let jobs: Job[] = [];
	let jobFilter = '';
	let jobId = '';
	let showDetail = false;
	let confirmDisconnect = false;
	let confirmSave = false;
	const controller = new AbortController();
	let revision = 0;
	$: displayedJobs = jobFilter ? jobs.filter((job) => job.status === jobFilter) : jobs;
	$: active = jobs.filter((job) => ['queued', 'processing'].includes(job.status)).length;
	$: parentFolder = folder.replace(/\/$/, '').split('/').slice(0, -1).join('/') || '/';
	async function refresh() {
		const current = ++revision;
		const result = await nextcloud<CloudConnection>(
			'/connection',
			'GET',
			undefined,
			controller.signal
		);
		const items = result.connected
			? (await nextcloud<{ jobs: Job[] }>('/jobs', 'GET', undefined, controller.signal)).jobs
			: [];
		if (current !== revision || controller.signal.aborted) return;
		connection = result;
		jobs = items;
		if (!result.connected) {
			files = [];
			sources = [];
			searched = false;
			browsed = false;
		}
	}
	async function action(work: () => Promise<void>) {
		busy = true;
		error = '';
		message = '';
		revision++;
		try {
			await work();
			await refresh();
		} catch (e) {
			if (!controller.signal.aborted) error = e instanceof Error ? e.message : 'Request failed';
		} finally {
			busy = false;
		}
	}
	async function browse(path = folder) {
		const result = await nextcloud<{ files: CloudFile[] }>(
			`/files?path=${encodeURIComponent(path)}`,
			'GET',
			undefined,
			controller.signal
		);
		folder = path;
		files = result.files.sort(
			(a, b) => Number(b.folder) - Number(a.folder) || a.path.localeCompare(b.path)
		);
		browsed = true;
	}
	async function search() {
		sources = (
			await nextcloud<{ sources: CloudSource[] }>(
				`/search?query=${encodeURIComponent(query)}`,
				'GET',
				undefined,
				controller.signal
			)
		).sources;
		searched = true;
	}
	async function connect() {
		const secret = password;
		password = '';
		await nextcloud(
			'/connection',
			'POST',
			{
				username,
				app_password: secret,
				scopes: scopes
					.split('\n')
					.map((s) => s.trim())
					.filter(Boolean)
			},
			controller.signal
		);
		await refresh();
		tab = 'files';
		await browse(connection.scopes?.[0] ?? '/');
		message = 'Account connected. Indexing is queued.';
	}
	async function save() {
		await nextcloud('/save', 'POST', { path: destination, text }, controller.signal);
		message = 'Save queued. Follow its progress in Activity.';
		tab = 'activity';
	}
	async function disconnect() {
		await nextcloud('/connection', 'DELETE', undefined, controller.signal);
		tab = 'connection';
		message = 'Disconnected. Your files remain in Nextcloud.';
	}
	async function control(id: string, operation: string) {
		await nextcloud(`/jobs/${id}`, 'POST', { operation }, controller.signal);
		message =
			operation === 'retry'
				? 'Retry queued.'
				: 'Cancellation requested. Files already saved remain in Nextcloud.';
	}
	onMount(() => {
		(async () => {
			await action(async () => {
				await refresh();
				if (connection.connected) await browse(connection.scopes?.[0] ?? '/');
				else tab = 'connection';
			});
			loaded = true;
		})();
		const timer = setInterval(() => {
			if (!busy && document.visibilityState === 'visible')
				refresh().catch((e) => {
					if (!controller.signal.aborted)
						error = e instanceof Error ? e.message : 'Unable to refresh';
				});
		}, 10000);
		return () => {
			clearInterval(timer);
			controller.abort();
		};
	});
</script>

<svelte:head><title>Nextcloud / {$WEBUI_NAME}</title></svelte:head>
<JobDetail bind:show={showDetail} {jobId} />
<ConfirmDialog
	bind:show={confirmDisconnect}
	title="Disconnect Nextcloud?"
	message="This removes your connection, index and processing history. Original files in Nextcloud are kept."
	confirmLabel="Disconnect"
	onConfirm={() => action(disconnect)}
/>
<ConfirmDialog
	bind:show={confirmSave}
	title="Save findings to Nextcloud?"
	message={`Create ${destination}. Anyone with access to that folder may be able to read this content. Existing files will not be overwritten.`}
	confirmLabel="Save file"
	onConfirm={() => action(save)}
/>
<div
	class="flex flex-col h-screen max-h-[100dvh] flex-1 min-w-0 w-full text-gray-900 dark:text-gray-100 {$showSidebar
		? 'md:max-w-[calc(100%-var(--sidebar-width))]'
		: 'md:max-w-[calc(100%-42px)]'}"
>
	<nav
		class="pb-1 px-2.5 pt-2 backdrop-blur-xl drag-region select-none"
		aria-label="Nextcloud sections"
	>
		<div class="flex items-center gap-1">
			{#if $mobile}<button
					class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-850"
					aria-label={$showSidebar ? 'Close sidebar' : 'Open sidebar'}
					on:click={() => showSidebar.set(!$showSidebar)}><Sidebar className="size-4" /></button
				>{/if}
			<div class="flex min-w-0 items-center gap-1 overflow-x-auto scrollbar-none py-1">
				{#each tabs as item}<button
						class="min-w-fit px-1 text-sm font-normal transition select-none {tab === item.id
							? 'text-gray-900 dark:text-gray-100'
							: 'text-gray-400 dark:text-gray-500 hover:text-gray-700 dark:hover:text-white'} disabled:opacity-40"
						disabled={!connection.connected && item.id !== 'connection'}
						aria-pressed={tab === item.id}
						on:click={() => (tab = item.id)}
					>
						{item.label}{#if item.id === 'activity' && active}<span class="ml-1 opacity-60"
								>{active}</span
							>{/if}
					</button>{/each}
			</div>
			{#if connection.url}<a
					class="ml-auto shrink-0 {ui.button}"
					href={connection.url}
					target="_blank"
					rel="noopener noreferrer"
					>Open Nextcloud <ArrowUpRight className="rotate-90 size-3.5" /></a
				>{/if}
		</div>
	</nav>
	<main class="pb-1 px-3 flex-1 min-w-0 overflow-y-auto overflow-x-hidden">
		<div class="space-y-2">
			{#if !loaded}<div class="py-20" role="status" aria-label="Loading Nextcloud">
					<Spinner className="size-6" />
				</div>{:else}
				{#if connection.connected}<div
						class="flex h-8 items-center justify-between gap-2 px-1 text-xs text-gray-500"
					>
						<span class="truncate" title={connection.scopes?.join(', ')}
							>{connection.username}
							<span class="ml-2 text-gray-400">{connection.scopes?.join(', ')}</span></span
						>
						<button
							class={ui.button}
							disabled={busy}
							on:click={() =>
								action(async () => {
									await nextcloud('/index', 'POST', {}, controller.signal);
									message = 'Index refresh queued.';
								})}>Refresh index</button
						>
					</div>{/if}
				{#if error}<div
						role="alert"
						class="rounded-xl bg-red-50 dark:bg-red-950/30 p-3 text-sm text-red-700 dark:text-red-300"
					>
						{error}
					</div>{/if}
				{#if message}<p role="status" class="text-sm text-gray-600 dark:text-gray-300">
						{message}
					</p>{/if}
				{#if busy}<div class="flex items-center gap-2 text-xs text-gray-500" role="status">
						<Spinner className="size-3" />Working…
					</div>{/if}
				{#if connection.connected && tab === 'files'}
					<section class="space-y-1" aria-label="Files">
						<div class="flex items-center justify-between">
							<h1 class="text-sm font-medium">Files</h1>
							<span class="text-xs text-gray-500">{files.length} items</span>
						</div>
						<form class="flex gap-2" on:submit|preventDefault={() => action(() => browse())}>
							<label class="grow"
								><span class="sr-only">Folder path</span><input
									class={ui.input}
									bind:value={folder}
									placeholder="/Documents"
									required
								/></label
							><button class={ui.button} disabled={busy}>Browse</button><button
								type="button"
								class={ui.button}
								disabled={busy || folder === '/'}
								on:click={() => action(() => browse(parentFolder))}>Up</button
							>
						</form>
						<div class="my-1">
							<div
								class="flex justify-between px-2 pb-0.5 text-xs text-gray-400 dark:text-gray-600"
							>
								<span>Name</span><span>Type</span>
							</div>
							<ul class="grid gap-y-0.5">
								{#each files as file (file.file_id)}<li>
										{#if file.folder}<button
												disabled={busy}
												class="group flex min-h-8 w-full items-center gap-2 rounded-xl px-2 py-1 text-left hover:bg-gray-100 dark:hover:bg-gray-850/50"
												on:click={() => action(() => browse(file.path))}
												><Folder className="size-3.5 text-gray-500 shrink-0" /><span
													class="text-[0.8125rem] leading-5 flex-1 truncate group-hover:underline"
													>{file.path.split('/').filter(Boolean).pop()}</span
												><span class="text-xs text-gray-500">Folder</span></button
											>{:else}<a
												class="group flex min-h-8 items-center gap-2 rounded-xl px-2 py-1 hover:bg-gray-100 dark:hover:bg-gray-850/50"
												href={file.url}
												target="_blank"
												rel="noopener noreferrer"
												><Document className="size-3.5 text-gray-500 shrink-0" /><span
													class="text-[0.8125rem] leading-5 flex-1 truncate group-hover:underline"
													>{file.path.split('/').pop()}</span
												><ArrowUpRight className="rotate-90 size-4 text-gray-400" /></a
											>{/if}
									</li>{/each}
							</ul>
							{#if browsed && !files.length}<div class="py-14 text-center">
									<Folder className="size-7 mx-auto text-gray-400" />
									<p class="text-sm font-medium mt-3">No files in this folder</p>
									<p class="text-xs text-gray-500 mt-1">
										Add files in Nextcloud or choose another folder.
									</p>
								</div>{/if}
						</div>
					</section>
				{:else if connection.connected && tab === 'search'}
					<section class="space-y-4">
						<div>
							<h2 class="text-sm font-medium">Search indexed content</h2>
							<p class="text-xs text-gray-500 mt-1">
								Search passages in files you can currently access.
							</p>
						</div>
						<form class="flex gap-2" on:submit|preventDefault={() => action(search)}>
							<div class="relative grow">
								<Search className="absolute left-2 top-2 size-3.5 text-gray-400" /><label
									class="sr-only"
									for="cloud-query">Search query</label
								><input
									id="cloud-query"
									class="{ui.input} pl-7"
									bind:value={query}
									required
									maxlength="4000"
									placeholder="Ask a question or search your documents"
								/>
							</div>
							<button class={ui.primary} disabled={busy}>Search</button>
						</form>
						{#if searched}<p class="text-xs text-gray-500">
								{sources.length} matching passages
							</p>{/if}
						{#each sources as source, index (`${source.file_id}-${index}`)}<article
								class="{ui.panel} space-y-3"
							>
								<a
									class="flex items-center gap-2 text-sm font-medium hover:underline"
									href={source.url}
									target="_blank"
									rel="noopener noreferrer"
									><Document className="size-4 shrink-0" /><span class="break-all"
										>{source.path}</span
									><ArrowUpRight className="rotate-90 size-3.5 shrink-0" /></a
								>
								<p
									class="text-sm leading-relaxed text-gray-600 dark:text-gray-300 whitespace-pre-wrap"
								>
									{source.text}
								</p>
								<button
									class="text-xs text-gray-500 hover:text-gray-900 dark:hover:text-white underline"
									disabled={busy}
									on:click={() =>
										action(async () => {
											await nextcloud(
												`/index/${encodeURIComponent(source.file_id)}`,
												'DELETE',
												undefined,
												controller.signal
											);
											sources = sources.filter((s) => s.file_id !== source.file_id);
											message = 'Excluded from your index. The original remains in Nextcloud.';
										})}>Exclude file from my index</button
								>
							</article>{/each}
						{#if searched && !sources.length}<div class="py-12 text-center">
								<Search className="size-7 mx-auto text-gray-400" />
								<p class="text-sm font-medium mt-3">No indexed passages available</p>
								<p class="text-xs text-gray-500 mt-1">
									Check your indexing folders and processing jobs, or refresh the index.
								</p>
							</div>{/if}
					</section>
				{:else if connection.connected && tab === 'save'}
					<section class="max-w-3xl space-y-4">
						<div>
							<h2 class="text-sm font-medium">Save findings</h2>
							<p class="text-xs text-gray-500 mt-1">
								Create a file in Nextcloud from selected findings.
							</p>
						</div>
						<form class="space-y-4" on:submit|preventDefault={() => (confirmSave = true)}>
							<label class="block text-sm font-medium"
								>Destination file<input
									class="{ui.input} mt-2"
									bind:value={destination}
									required
									maxlength="1000"
								/><span class="block text-xs font-normal text-gray-500 mt-1.5"
									>The destination folder must exist. Existing files are never overwritten.</span
								></label
							><label class="block text-sm font-medium"
								>Content<textarea
									class="{ui.input} mt-2 min-h-64 resize-y leading-relaxed"
									bind:value={text}
									required
									rows="10"
									placeholder="Enter the findings you want to save"
								></textarea></label
							>
							<div class="flex flex-wrap items-center justify-between gap-3">
								<p class="text-xs text-gray-500">
									The file inherits the folder's sharing permissions.
								</p>
								<button class={ui.primary} disabled={busy}>Save to Nextcloud</button>
							</div>
						</form>
					</section>
				{:else if connection.connected && tab === 'activity'}
					<section class="space-y-4">
						<div class="flex items-center justify-between gap-3">
							<h2 class="text-sm font-medium">My processing jobs</h2>
							<label
								><span class="sr-only">Filter job status</span><select
									class={ui.input}
									bind:value={jobFilter}
									><option value="">All statuses</option
									>{#each ['queued', 'processing', 'completed', 'failed', 'interrupted', 'cancelled'] as value}<option
											{value}>{label(value)}</option
										>{/each}</select
								></label
							>
						</div>
						<p class="text-xs text-gray-500">
							Latest 100 jobs. Saving a file and indexing it are separate jobs.
						</p>
						<div class="space-y-2">
							{#each displayedJobs as job (job.id)}<article
									class="{ui.panel} flex flex-wrap justify-between gap-4"
								>
									<div class="space-y-2">
										<div class="flex items-center gap-2">
											<h3 class="text-sm font-medium">{label(job.action)}</h3>
											<Badge type={badge(job.status)} content={label(job.status)} />
										</div>
										<p class="text-xs text-gray-500">
											{date(job.created)}{#if job.stage !== job.status}<span class="ml-3"
													>{label(job.stage)}</span
												>{/if}
										</p>
										{#if job.result.indexed !== undefined}<p class="text-xs text-gray-500">
												{job.result.indexed} indexed files
											</p>{/if}{#if job.error}<p
												class="text-sm text-red-600 dark:text-red-400 max-w-xl"
											>
												{job.error}
											</p>{/if}
									</div>
									<div class="flex flex-wrap items-center gap-2">
										<button
											class={ui.button}
											on:click={() => {
												jobId = job.id;
												showDetail = true;
											}}>View log</button
										>{#if ['queued', 'processing'].includes(job.status)}<button
												class={ui.button}
												disabled={busy || !!job.cancel}
												on:click={() => action(() => control(job.id, 'cancel'))}
												>{job.cancel ? 'Cancelling…' : 'Cancel'}</button
											>{/if}{#if ['failed', 'interrupted', 'cancelled'].includes(job.status)}<button
												class={ui.button}
												disabled={busy}
												on:click={() => action(() => control(job.id, 'retry'))}>Retry</button
											>{/if}{#if job.result.url}<a
												class={ui.button}
												href={job.result.url}
												target="_blank"
												rel="noopener noreferrer"
												>Open file <ArrowUpRight className="rotate-90 size-4" /></a
											>{/if}
									</div>
								</article>{/each}{#if !displayedJobs.length}<p
									class="py-12 text-center text-sm text-gray-500"
								>
									No jobs match this status.
								</p>{/if}
						</div>
					</section>
				{:else}
					<section class="max-w-2xl space-y-5">
						<div>
							<h2 class="text-sm font-medium">Account connection</h2>
							<p class="text-xs text-gray-500 mt-1">
								Connect your own account to access files under your Nextcloud permissions.
							</p>
						</div>
						{#if connection.connected}<div class="{ui.panel} space-y-4">
								<div class="flex items-center gap-2">
									<Badge type="success" content="Connected" /><span class="text-sm font-medium"
										>{connection.username}</span
									>
								</div>
								<div>
									<h3 class="text-xs text-gray-500 mb-2">Indexing folders</h3>
									<ul class="space-y-1">
										{#each connection.scopes ?? [] as scope}<li
												class="flex items-center gap-2 text-sm"
											>
												<Folder className="size-4 text-gray-400" />{scope}
											</li>{/each}
									</ul>
								</div>
								<p class="text-xs text-gray-500">
									To change the account or indexing folders, disconnect and connect again.
								</p>
								<button
									class="{ui.button} text-red-600 dark:text-red-400"
									disabled={busy}
									on:click={() => (confirmDisconnect = true)}>Disconnect account</button
								>
							</div>{:else}<p class="text-sm text-gray-500">
								Create an app password in Nextcloud under Personal settings, Security. Choose
								existing folders to index.
							</p>
							<form class="space-y-4" on:submit|preventDefault={() => action(connect)}>
								<label class="block text-sm font-medium"
									>Nextcloud username<input
										class="{ui.input} mt-2"
										bind:value={username}
										required
										autocomplete="username"
									/></label
								><label class="block text-sm font-medium"
									>App password<input
										class="{ui.input} mt-2"
										type="password"
										bind:value={password}
										required
										autocomplete="new-password"
									/></label
								><label class="block text-sm font-medium"
									>Indexing folders<textarea
										class="{ui.input} mt-2"
										bind:value={scopes}
										required
										rows="3"
									></textarea><span class="block text-xs text-gray-500 font-normal mt-1.5"
										>One absolute folder path per line.</span
									></label
								><button class={ui.primary} disabled={busy}>Connect account</button>
							</form>{/if}
					</section>
				{/if}
			{/if}
		</div>
	</main>
</div>
