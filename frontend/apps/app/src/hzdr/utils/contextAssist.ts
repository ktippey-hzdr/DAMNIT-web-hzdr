import type { ColumnSuggestion, HZDRDatasetOption } from '../types'

import { buildComputedFieldSnippet, pythonNameFromTitle } from './context'
import { formatShape } from './hdf5'

/** How many HDF5 datasets of the example shot are offered at most. */
export const MAX_DATASET_SUGGESTIONS = 8

const DATASET_DISPLAYS: Record<
  string,
  { display: string; displayLabel: string; displayHelp: string; what: string }
> = {
  image: {
    display: 'image-preview',
    displayLabel: 'Image preview',
    displayHelp:
      'Shows the mean of the image in each row; double-click a cell to see the image itself.',
    what: 'an image',
  },
  line: {
    display: 'lineout-preview',
    displayLabel: 'Lineout preview',
    displayHelp:
      'Shows the mean of the lineout in each row; double-click a cell to see the curve.',
    what: 'a lineout',
  },
  scalar: {
    display: 'hdf5',
    displayLabel: 'Number column',
    displayHelp: 'Shows the stored number in each shot’s row.',
    what: 'one number',
  },
}

function datasetTitle(name: string) {
  const last =
    name
      .replace(/\{shot_id\}/g, 'shot')
      .split('/')
      .filter(Boolean)
      .at(-1) ?? name
  const text = last.replace(/[_-]+/g, ' ').trim()
  return text.charAt(0).toUpperCase() + text.slice(1)
}

/**
 * Column suggestions for the example shot's HDF5 datasets: images become an
 * image preview, lineouts a lineout preview, scalars a number. Raw and
 * multi-shot stacks are left to the manual builder.
 */
export function datasetSuggestions(
  options: HZDRDatasetOption[],
  exampleLabel: string
): ColumnSuggestion[] {
  const suggestions: ColumnSuggestion[] = []
  const taken = new Set<string>()
  for (const option of options) {
    const recipe = DATASET_DISPLAYS[option.kind]
    if (!recipe || suggestions.length >= MAX_DATASET_SUGGESTIONS) continue
    const title = datasetTitle(option.name)
    let fieldName = pythonNameFromTitle(title) || 'dataset'
    for (let index = 2; taken.has(fieldName); index += 1) {
      fieldName = `${pythonNameFromTitle(title)}_${index}`
    }
    taken.add(fieldName)
    suggestions.push({
      id: `hdf5:${option.name}`,
      title,
      function: fieldName,
      origin: 'hdf5',
      originLabel: 'HDF5 dataset',
      field: option.name,
      path: option.name,
      type: option.kind,
      unit: '',
      count: 1,
      total: 1,
      examples: [],
      reason: `On the example shot (${exampleLabel}): ${recipe.what}, ${option.dtype} ${formatShape(option.shape)}, at ${option.previewName}.`,
      starter: false,
      block: buildComputedFieldSnippet({
        fieldKind: recipe.display,
        contextScope: 'shot',
        fieldName,
        fieldTitle: title,
        selectedInputs: [option.value],
        mongoCollection: 'shots',
        mongoFilter: '{}',
        combineExpression: '',
      }),
      display: recipe.display,
      displayLabel: recipe.displayLabel,
      displayHelp: recipe.displayHelp,
    })
  }
  return suggestions
}

/**
 * True when the context file already reads this suggestion's data: its path
 * appears quoted in the file, or a column has its function name.
 */
export function isAlreadyInContext(
  suggestion: ColumnSuggestion,
  contextContent: string,
  existingNames: Set<string>
) {
  if (existingNames.has(suggestion.function)) return true
  return [`"${suggestion.path}"`, `'${suggestion.path}'`].some((quoted) =>
    contextContent.includes(quoted)
  )
}

/** The builder form state that reproduces a suggestion, for Customise. */
export function suggestionFormState(suggestion: ColumnSuggestion) {
  if (suggestion.origin === 'hdf5') {
    return {
      fieldKind: suggestion.display,
      selectedInputs: [`hdf5:${suggestion.field}`],
      fieldTitle: suggestion.title,
    }
  }
  return {
    fieldKind: 'metadata',
    selectedInputs: [`metadata:${suggestion.field}`],
    fieldTitle: suggestion.title,
  }
}

const IGNORED_PREFIX = 'hzdr.contextAssist.ignored.'

/** The suggestions this browser was told to ignore for a campaign. */
export function loadIgnoredSuggestions(campaign: string): string[] {
  try {
    const stored = JSON.parse(
      window.localStorage.getItem(`${IGNORED_PREFIX}${campaign}`) ?? '[]'
    ) as unknown
    return Array.isArray(stored)
      ? stored.filter((id): id is string => typeof id === 'string')
      : []
  } catch {
    return []
  }
}

export function saveIgnoredSuggestions(campaign: string, ids: string[]) {
  try {
    if (ids.length) {
      window.localStorage.setItem(
        `${IGNORED_PREFIX}${campaign}`,
        JSON.stringify(ids)
      )
    } else {
      window.localStorage.removeItem(`${IGNORED_PREFIX}${campaign}`)
    }
  } catch {
    // Private windows and blocked storage: ignoring lasts for this visit.
  }
}
