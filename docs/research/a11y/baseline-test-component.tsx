import React, {
	useState,
	useRef,
	useEffect,
	useCallback,
	useId,
	KeyboardEvent,
	MouseEvent,
	ChangeEvent,
} from 'react';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Tag {
	id: string;
	label: string;
}

interface TagSelectorProps {
	/** All tags available to pick from. */
	availableTags: Tag[];
	/** Initially selected tag ids. */
	defaultSelectedIds?: string[];
	/** Called with the final selected tags when the user confirms. */
	onConfirm?: ( tags: Tag[] ) => void;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function filterTags( tags: Tag[], query: string ): Tag[] {
	const q = query.trim().toLowerCase();
	if ( ! q ) return tags;
	return tags.filter( ( t ) => t.label.toLowerCase().includes( q ) );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

interface TokenProps {
	tag: Tag;
	onRemove: ( id: string ) => void;
}

function Token( { tag, onRemove }: TokenProps ) {
	return (
		<span
			style={ styles.token }
			role="listitem"
		>
			{ tag.label }
			<button
				type="button"
				aria-label={ `Remove ${ tag.label }` }
				onClick={ () => onRemove( tag.id ) }
				style={ styles.tokenRemove }
			>
				{ /* multiplication sign: visually clear, safe for screen readers */ }
				&times;
			</button>
		</span>
	);
}

// ---------------------------------------------------------------------------
// Modal
// ---------------------------------------------------------------------------

interface ModalProps {
	isOpen: boolean;
	onClose: () => void;
	title: string;
	children: React.ReactNode;
}

function Modal( { isOpen, onClose, title, children }: ModalProps ) {
	const dialogRef = useRef< HTMLDivElement >( null );
	const titleId = useId();

	// Trap focus inside the modal while it is open.
	useEffect( () => {
		if ( ! isOpen ) return;

		const dialog = dialogRef.current;
		if ( ! dialog ) return;

		const focusableSelectors = [
			'a[href]',
			'button:not([disabled])',
			'input:not([disabled])',
			'select:not([disabled])',
			'textarea:not([disabled])',
			'[tabindex]:not([tabindex="-1"])',
		].join( ', ' );

		const getFocusable = () =>
			Array.from( dialog.querySelectorAll< HTMLElement >( focusableSelectors ) );

		const handleKeyDown = ( e: globalThis.KeyboardEvent ) => {
			if ( e.key === 'Escape' ) {
				onClose();
				return;
			}
			if ( e.key !== 'Tab' ) return;

			const focusable = getFocusable();
			if ( focusable.length === 0 ) return;

			const first = focusable[ 0 ];
			const last = focusable[ focusable.length - 1 ];

			if ( e.shiftKey ) {
				if ( document.activeElement === first ) {
					e.preventDefault();
					last.focus();
				}
			} else {
				if ( document.activeElement === last ) {
					e.preventDefault();
					first.focus();
				}
			}
		};

		document.addEventListener( 'keydown', handleKeyDown );

		// Move focus into the modal on open.
		const firstFocusable = getFocusable()[ 0 ];
		if ( firstFocusable ) {
			firstFocusable.focus();
		} else {
			dialog.focus();
		}

		// Prevent background scroll.
		document.body.style.overflow = 'hidden';

		return () => {
			document.removeEventListener( 'keydown', handleKeyDown );
			document.body.style.overflow = '';
		};
	}, [ isOpen, onClose ] );

	if ( ! isOpen ) return null;

	return (
		<div
			style={ styles.backdrop }
			onClick={ ( e: MouseEvent ) => {
				// Close when clicking outside the dialog box.
				if ( e.target === e.currentTarget ) onClose();
			} }
			aria-modal="true"
		>
			<div
				ref={ dialogRef }
				role="dialog"
				aria-labelledby={ titleId }
				style={ styles.dialog }
				tabIndex={ -1 }
			>
				{ /* Header */ }
				<div style={ styles.dialogHeader }>
					<h2 id={ titleId } style={ styles.dialogTitle }>
						{ title }
					</h2>
					<button
						type="button"
						aria-label="Close dialog"
						onClick={ onClose }
						style={ styles.closeButton }
					>
						&times;
					</button>
				</div>

				{ /* Body */ }
				<div style={ styles.dialogBody }>{ children }</div>
			</div>
		</div>
	);
}

// ---------------------------------------------------------------------------
// Combobox + selected-token area
// ---------------------------------------------------------------------------

interface TagComboboxProps {
	availableTags: Tag[];
	selectedIds: Set< string >;
	onSelect: ( id: string ) => void;
	onDeselect: ( id: string ) => void;
}

function TagCombobox( {
	availableTags,
	selectedIds,
	onSelect,
	onDeselect,
}: TagComboboxProps ) {
	const [ query, setQuery ] = useState( '' );
	const [ isOpen, setIsOpen ] = useState( false );
	const [ activeIndex, setActiveIndex ] = useState< number | null >( null );

	const inputRef = useRef< HTMLInputElement >( null );
	const listRef = useRef< HTMLUListElement >( null );
	const comboboxId = useId();
	const listboxId = `${ comboboxId }-listbox`;

	const filtered = filterTags( availableTags, query );

	// Scroll active option into view.
	useEffect( () => {
		if ( activeIndex === null || ! listRef.current ) return;
		const items = listRef.current.querySelectorAll< HTMLLIElement >( '[role="option"]' );
		items[ activeIndex ]?.scrollIntoView( { block: 'nearest' } );
	}, [ activeIndex ] );

	const openList = useCallback( () => {
		setIsOpen( true );
		setActiveIndex( null );
	}, [] );

	const closeList = useCallback( () => {
		setIsOpen( false );
		setActiveIndex( null );
	}, [] );

	const toggleTag = useCallback(
		( id: string ) => {
			if ( selectedIds.has( id ) ) {
				onDeselect( id );
			} else {
				onSelect( id );
			}
		},
		[ selectedIds, onSelect, onDeselect ]
	);

	const handleInputChange = ( e: ChangeEvent< HTMLInputElement > ) => {
		setQuery( e.target.value );
		setActiveIndex( null );
		if ( ! isOpen ) setIsOpen( true );
	};

	const handleInputKeyDown = ( e: KeyboardEvent< HTMLInputElement > ) => {
		switch ( e.key ) {
			case 'ArrowDown': {
				e.preventDefault();
				if ( ! isOpen ) {
					openList();
					setActiveIndex( 0 );
				} else {
					setActiveIndex( ( prev ) =>
						prev === null ? 0 : Math.min( prev + 1, filtered.length - 1 )
					);
				}
				break;
			}
			case 'ArrowUp': {
				e.preventDefault();
				if ( isOpen ) {
					setActiveIndex( ( prev ) =>
						prev === null ? filtered.length - 1 : Math.max( prev - 1, 0 )
					);
				}
				break;
			}
			case 'Enter': {
				e.preventDefault();
				if ( isOpen && activeIndex !== null && filtered[ activeIndex ] ) {
					toggleTag( filtered[ activeIndex ].id );
					setQuery( '' );
					setActiveIndex( null );
				}
				break;
			}
			case 'Escape': {
				// Intercept before the modal's own Escape handler closes the dialog:
				// if the listbox is open, close it first; a second Escape will close the modal.
				if ( isOpen ) {
					e.stopPropagation();
					closeList();
				}
				break;
			}
			case 'Tab': {
				closeList();
				break;
			}
		}
	};

	const activeOptionId =
		isOpen && activeIndex !== null && filtered[ activeIndex ]
			? `${ listboxId }-option-${ activeIndex }`
			: undefined;

	return (
		<div style={ styles.comboboxWrapper }>
			{ /* Selected tokens */ }
			{ selectedIds.size > 0 && (
				<div
					role="list"
					aria-label="Selected tags"
					style={ styles.tokenList }
				>
					{ Array.from( selectedIds ).map( ( id ) => {
						const tag = availableTags.find( ( t ) => t.id === id );
						if ( ! tag ) return null;
						return (
							<Token
								key={ id }
								tag={ tag }
								onRemove={ onDeselect }
							/>
						);
					} ) }
				</div>
			) }

			{ /* Input */ }
			<div style={ styles.inputWrapper }>
				<input
					ref={ inputRef }
					id={ comboboxId }
					type="text"
					role="combobox"
					aria-expanded={ isOpen }
					aria-controls={ listboxId }
					aria-autocomplete="list"
					aria-activedescendant={ activeOptionId }
					aria-label="Search tags"
					placeholder="Search tags…"
					value={ query }
					onChange={ handleInputChange }
					onFocus={ openList }
					onBlur={ () => {
						// Delay so clicks on list items register first.
						setTimeout( closeList, 150 );
					} }
					onKeyDown={ handleInputKeyDown }
					style={ styles.input }
					autoComplete="off"
				/>
			</div>

			{ /* Dropdown */ }
			{ isOpen && (
				<ul
					ref={ listRef }
					id={ listboxId }
					role="listbox"
					aria-label="Available tags"
					aria-multiselectable="true"
					style={ styles.listbox }
				>
					{ filtered.length === 0 ? (
						<li style={ styles.noResults } aria-live="polite">
							No tags match "{ query }"
						</li>
					) : (
						filtered.map( ( tag, index ) => {
							const selected = selectedIds.has( tag.id );
							const isActive = index === activeIndex;
							return (
								<li
									key={ tag.id }
									id={ `${ listboxId }-option-${ index }` }
									role="option"
									aria-selected={ selected }
									onMouseDown={ ( e: MouseEvent ) => {
										// Prevent input blur before click registers.
										e.preventDefault();
										toggleTag( tag.id );
										setQuery( '' );
										inputRef.current?.focus();
									} }
									style={ {
										...styles.option,
										...( isActive ? styles.optionActive : {} ),
										...( selected ? styles.optionSelected : {} ),
									} }
								>
									<span
										style={ styles.optionCheckbox }
										aria-hidden="true"
									>
										{ selected ? '✓' : '' }
									</span>
									{ tag.label }
								</li>
							);
						} )
					) }
				</ul>
			) }
		</div>
	);
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function TagSelector( {
	availableTags,
	defaultSelectedIds = [],
	onConfirm,
}: TagSelectorProps ) {
	const [ isModalOpen, setIsModalOpen ] = useState( false );

	// Draft state — mutations happen here; only committed on "Done".
	const [ draftSelectedIds, setDraftSelectedIds ] = useState< Set< string > >(
		() => new Set( defaultSelectedIds )
	);

	// Confirmed state — shown outside the modal.
	const [ confirmedIds, setConfirmedIds ] = useState< Set< string > >(
		() => new Set( defaultSelectedIds )
	);

	const openModal = () => {
		// Reset draft to current confirmed state each time the modal opens.
		setDraftSelectedIds( new Set( confirmedIds ) );
		setIsModalOpen( true );
	};

	const closeModal = () => {
		setIsModalOpen( false );
	};

	const cancelModal = () => {
		// Discard draft changes.
		setDraftSelectedIds( new Set( confirmedIds ) );
		closeModal();
	};

	const confirmSelection = () => {
		setConfirmedIds( new Set( draftSelectedIds ) );
		const selected = availableTags.filter( ( t ) => draftSelectedIds.has( t.id ) );
		onConfirm?.( selected );
		closeModal();
	};

	const handleSelect = useCallback( ( id: string ) => {
		setDraftSelectedIds( ( prev ) => new Set( [ ...prev, id ] ) );
	}, [] );

	const handleDeselect = useCallback( ( id: string ) => {
		setDraftSelectedIds( ( prev ) => {
			const next = new Set( prev );
			next.delete( id );
			return next;
		} );
	}, [] );

	const confirmedTags = availableTags.filter( ( t ) => confirmedIds.has( t.id ) );

	return (
		<div style={ styles.root }>
			{ /* Trigger area */ }
			<div style={ styles.triggerArea }>
				{ confirmedTags.length > 0 ? (
					<div style={ styles.confirmedTagsArea }>
						<span style={ styles.confirmedLabel }>Tags:</span>
						{ confirmedTags.map( ( t ) => (
							<span key={ t.id } style={ styles.confirmedTag }>
								{ t.label }
							</span>
						) ) }
					</div>
				) : (
					<span style={ styles.noTagsLabel }>No tags selected</span>
				) }
				<button
					type="button"
					onClick={ openModal }
					style={ styles.manageTrigger }
				>
					Manage Tags
				</button>
			</div>

			{ /* Modal */ }
			<Modal
				isOpen={ isModalOpen }
				onClose={ cancelModal }
				title="Select Tags"
			>
				<TagCombobox
					availableTags={ availableTags }
					selectedIds={ draftSelectedIds }
					onSelect={ handleSelect }
					onDeselect={ handleDeselect }
				/>

				{ /* Footer actions */ }
				<div style={ styles.dialogFooter }>
					<button
						type="button"
						onClick={ cancelModal }
						style={ { ...styles.button, ...styles.buttonSecondary } }
					>
						Cancel
					</button>
					<button
						type="button"
						onClick={ confirmSelection }
						style={ { ...styles.button, ...styles.buttonPrimary } }
					>
						Done
					</button>
				</div>
			</Modal>
		</div>
	);
}

// ---------------------------------------------------------------------------
// Inline styles
// (In a real app you would use CSS modules, Tailwind, or a design system.)
// ---------------------------------------------------------------------------

const styles: Record< string, React.CSSProperties > = {
	root: {
		fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
		fontSize: '14px',
	},

	// Trigger
	triggerArea: {
		display: 'flex',
		alignItems: 'center',
		gap: '12px',
		flexWrap: 'wrap',
	},
	confirmedTagsArea: {
		display: 'flex',
		alignItems: 'center',
		gap: '6px',
		flexWrap: 'wrap',
	},
	confirmedLabel: {
		fontWeight: 600,
		color: '#374151',
	},
	confirmedTag: {
		display: 'inline-flex',
		alignItems: 'center',
		padding: '2px 10px',
		borderRadius: '12px',
		background: '#EEF2FF',
		color: '#4338CA',
		fontWeight: 500,
		fontSize: '12px',
	},
	noTagsLabel: {
		color: '#9CA3AF',
		fontStyle: 'italic',
	},
	manageTrigger: {
		padding: '8px 16px',
		borderRadius: '6px',
		border: '1px solid #D1D5DB',
		background: '#FFFFFF',
		color: '#111827',
		fontWeight: 600,
		fontSize: '14px',
		cursor: 'pointer',
		transition: 'background 0.15s',
	},

	// Backdrop + dialog
	backdrop: {
		position: 'fixed',
		inset: 0,
		background: 'rgba(0,0,0,0.45)',
		display: 'flex',
		alignItems: 'center',
		justifyContent: 'center',
		zIndex: 1000,
	},
	dialog: {
		background: '#FFFFFF',
		borderRadius: '10px',
		boxShadow: '0 20px 60px rgba(0,0,0,0.25)',
		width: '480px',
		maxWidth: '95vw',
		maxHeight: '80vh',
		display: 'flex',
		flexDirection: 'column',
		overflow: 'hidden',
		outline: 'none',
	},
	dialogHeader: {
		display: 'flex',
		alignItems: 'center',
		justifyContent: 'space-between',
		padding: '16px 20px',
		borderBottom: '1px solid #E5E7EB',
	},
	dialogTitle: {
		margin: 0,
		fontSize: '16px',
		fontWeight: 700,
		color: '#111827',
	},
	closeButton: {
		background: 'none',
		border: 'none',
		fontSize: '22px',
		lineHeight: 1,
		cursor: 'pointer',
		color: '#6B7280',
		padding: '2px 6px',
		borderRadius: '4px',
	},
	dialogBody: {
		flex: 1,
		overflowY: 'auto',
		padding: '16px 20px',
		display: 'flex',
		flexDirection: 'column',
		gap: '12px',
	},
	dialogFooter: {
		display: 'flex',
		justifyContent: 'flex-end',
		gap: '8px',
		padding: '12px 20px',
		borderTop: '1px solid #E5E7EB',
	},

	// Buttons
	button: {
		padding: '8px 18px',
		borderRadius: '6px',
		fontSize: '14px',
		fontWeight: 600,
		cursor: 'pointer',
		border: '1px solid transparent',
		transition: 'background 0.15s, border-color 0.15s',
	},
	buttonPrimary: {
		background: '#4F46E5',
		color: '#FFFFFF',
		borderColor: '#4F46E5',
	},
	buttonSecondary: {
		background: '#FFFFFF',
		color: '#374151',
		borderColor: '#D1D5DB',
	},

	// Combobox
	comboboxWrapper: {
		position: 'relative',
		display: 'flex',
		flexDirection: 'column',
		gap: '10px',
	},
	tokenList: {
		display: 'flex',
		flexWrap: 'wrap',
		gap: '6px',
	},
	token: {
		display: 'inline-flex',
		alignItems: 'center',
		gap: '4px',
		padding: '3px 10px 3px 12px',
		borderRadius: '14px',
		background: '#EEF2FF',
		color: '#4338CA',
		fontWeight: 500,
		fontSize: '13px',
	},
	tokenRemove: {
		background: 'none',
		border: 'none',
		cursor: 'pointer',
		color: '#6366F1',
		fontSize: '16px',
		lineHeight: 1,
		padding: '0 2px',
		borderRadius: '50%',
		display: 'inline-flex',
		alignItems: 'center',
		justifyContent: 'center',
	},
	inputWrapper: {
		position: 'relative',
	},
	input: {
		width: '100%',
		padding: '9px 12px',
		border: '1px solid #D1D5DB',
		borderRadius: '6px',
		fontSize: '14px',
		color: '#111827',
		outline: 'none',
		boxSizing: 'border-box',
		background: '#FAFAFA',
	},
	listbox: {
		listStyle: 'none',
		margin: 0,
		padding: '4px 0',
		border: '1px solid #E5E7EB',
		borderRadius: '6px',
		background: '#FFFFFF',
		boxShadow: '0 4px 16px rgba(0,0,0,0.10)',
		maxHeight: '220px',
		overflowY: 'auto',
	},
	option: {
		display: 'flex',
		alignItems: 'center',
		gap: '10px',
		padding: '8px 14px',
		cursor: 'pointer',
		color: '#111827',
		userSelect: 'none',
	},
	optionActive: {
		background: '#EEF2FF',
	},
	optionSelected: {
		fontWeight: 600,
		color: '#4338CA',
	},
	optionCheckbox: {
		width: '16px',
		textAlign: 'center',
		color: '#4F46E5',
		flexShrink: 0,
		fontSize: '13px',
	},
	noResults: {
		padding: '10px 14px',
		color: '#9CA3AF',
		fontStyle: 'italic',
	},
};

// ---------------------------------------------------------------------------
// Demo / usage example
// ---------------------------------------------------------------------------

const DEMO_TAGS: Tag[] = [
	{ id: 'react', label: 'React' },
	{ id: 'typescript', label: 'TypeScript' },
	{ id: 'accessibility', label: 'Accessibility' },
	{ id: 'performance', label: 'Performance' },
	{ id: 'testing', label: 'Testing' },
	{ id: 'css', label: 'CSS' },
	{ id: 'animation', label: 'Animation' },
	{ id: 'state-management', label: 'State Management' },
	{ id: 'forms', label: 'Forms' },
	{ id: 'routing', label: 'Routing' },
	{ id: 'i18n', label: 'Internationalisation' },
	{ id: 'security', label: 'Security' },
];

export default function App() {
	const [ confirmed, setConfirmed ] = useState< Tag[] >( [] );

	return (
		<div style={ { padding: '40px', maxWidth: '640px', margin: '0 auto' } }>
			<h1 style={ { marginBottom: '24px', fontSize: '20px', fontWeight: 700 } }>
				Tag Selector — Demo
			</h1>

			<TagSelector
				availableTags={ DEMO_TAGS }
				defaultSelectedIds={ [ 'react', 'typescript' ] }
				onConfirm={ setConfirmed }
			/>

			{ confirmed.length > 0 && (
				<p style={ { marginTop: '24px', color: '#6B7280', fontSize: '13px' } }>
					Last confirmed:{ ' ' }
					<strong>{ confirmed.map( ( t ) => t.label ).join( ', ' ) }</strong>
				</p>
			) }
		</div>
	);
}
