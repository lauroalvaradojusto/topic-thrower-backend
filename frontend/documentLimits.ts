/**
 * Document Limits - Frontend Validation Utilities
 * 
 * Límites del sistema:- 20 documentos por mes
 * - 299 MB máximo almacenamiento total
 * - 50 MB máximo por archivo
 */

export const LIMITS = {
  MAX_DOCS_PER_MONTH: 20,
  MAX_TOTAL_STORAGE_MB: 299,
  MAX_FILE_SIZE_MB: 50,
  ALLOWED_FILE_TYPES: [
    'application/pdf',
    'text/plain',
    'text/html',
    'text/markdown',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
  ] as const,
} as const;

export type AllowedFileType = typeof LIMITS.ALLOWED_FILE_TYPES[number];

export interface UserLimits {
  documents: {
    current: number;
    max: number;
  };
  storage: {
    currentMb: number;
    maxMb: number;
    currentBytes: number;
    maxBytes: number;
  };
  resetDate: string;
}

export interface ValidationResult {
  valid: boolean;
  errors: string[];
  warnings: string[];
}

export interface FileValidation extends ValidationResult {
  fileSize: number;
  fileName: string;
}

/**
 * Formatea bytes a una representaciónlegible
 */
export function formatBytes(bytes: number, decimals = 2): string {
  if (bytes === 0) return '0 Bytes';
  
  const k = 1024;
  const sizes = ['Bytes', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  
  return parseFloat((bytes / Math.pow(k, i)).toFixed(decimals)) + ' ' + sizes[i];
}

/**
 * Valida un archivo antes del upload
 */
export function validateFile(file: File): FileValidation {
  const errors: string[] = [];
  const warnings: string[] = [];
  
  // Check file type
  if (!LIMITS.ALLOWED_FILE_TYPES.includes(file.type as AllowedFileType)) {
    errors.push(
      `Tipo de archivo no permitido: ${file.type}. ` +
      `Tipos permitidos: PDF, TXT, HTML, MD, DOC, DOCX`
    );
  }
  
  // Check file size
  const fileSizeMB = file.size / (1024 * 1024);
  if (fileSizeMB > LIMITS.MAX_FILE_SIZE_MB) {
    errors.push(
      `Archivo muy grande: ${formatBytes(file.size)}. ` +
      `Máximo permitido: ${LIMITS.MAX_FILE_SIZE_MB} MB`
    );
  }
  
  // Warning for large files (over 30MB)
  if (fileSizeMB >30 && fileSizeMB <= LIMITS.MAX_FILE_SIZE_MB) {
    warnings.push(
      `Archivo grande (${formatBytes(file.size)}). ` +
      `El procesamiento puede tomar más tiempo.`
    );
  }
  
  return {
    valid: errors.length === 0,
    errors,
    warnings,
    fileSize: file.size,
    fileName: file.name,
  };
}

/**
 * Valida múltiples archivos antes del upload
 */
export function validateFiles(files: File[], currentLimits: UserLimits): ValidationResult {
  const errors: string[] = [];
  const warnings: string[] = [];
  
  // Check document count after upload
  const newDocCount = currentLimits.documents.current + files.length;
  if (newDocCount > LIMITS.MAX_DOCS_PER_MONTH) {
    const remaining = LIMITS.MAX_DOCS_PER_MONTH - currentLimits.documents.current;
    errors.push(
      `Límite de documentos excedido. ` +
      `Puedes subir ${remaining} documento(s) más este mes.`
    );
  }
  
  // Calculate total size after upload
  const totalSizeBytes = files.reduce((sum, f) => sum + f.size, 0);
  const newSizeBytes = currentLimits.storage.currentBytes + totalSizeBytes;
  const maxSizeBytes = LIMITS.MAX_TOTAL_STORAGE_MB * 1024 * 1024;
  
  if (newSizeBytes > maxSizeBytes) {
    const remainingBytes = maxSizeBytes - currentLimits.storage.currentBytes;
    errors.push(
      `Límite de almacenamiento excedido. ` +
      `Disponible: ${formatBytes(remainingBytes)}. ` +
      `Intentando subir: ${formatBytes(totalSizeBytes)}.`
    );
  }
  
  // Validate individual files
  for (const file of files) {
    const validation = validateFile(file);
    errors.push(...validation.errors);
    warnings.push(...validation.warnings);
  }
  
  return {
    valid: errors.length === 0,
    errors: [...new Set(errors)], // Remove duplicates
    warnings: [...new Set(warnings)],
  };
}

/**
 * Calcula el porcentaje de uso
 */
export function calculateUsagePercentage(limits: UserLimits): {
  docsPercentage: number;
  storagePercentage: number;
} {
  return {
    docsPercentage: (limits.documents.current / limits.documents.max) * 100,
    storagePercentage: (limits.storage.currentMb / limits.storage.maxMb) * 100,
  };
}

/**
 * Determina si mostrar advertencia de límite
 */
export function shouldShowLimitWarning(limits: UserLimits): {
  show: boolean;
  type: 'docs' | 'storage' | 'both' | null;
  message: string;
} {
  const { docsPercentage, storagePercentage } = calculateUsagePercentage(limits);
  
  if (docsPercentage >=80 && storagePercentage >= 80) {
    return {
      show: true,
      type: 'both',
      message: `Estás cerca de tus límites: ${limits.documents.current}/${limits.documents.max} documentos y ${limits.storage.currentMb.toFixed(1)}/${limits.storage.maxMb} MB de almacenamiento.`,
    };
  }
  
  if (docsPercentage >= 80) {
    return {
      show: true,
      type: 'docs',
      message: `Has usado ${limits.documents.current} de ${limits.documents.max} documentos este mes.`,
    };
  }
  
  if (storagePercentage >= 80) {
    return {
      show: true,
      type: 'storage',
      message: `Has usado ${limits.storage.currentMb.toFixed(1)} de ${limits.storage.maxMb} MB de almacenamiento.`,
    };
  }
  
  return { show: false, type: null, message: '' };
}

/**
 * Hook para obtener límites del usuario desde la API
 */
export async function fetchUserLimits(
  supabaseUrl: string,
  token: string
): Promise<UserLimits> {
  const response = await fetch(
    `${supabaseUrl}/functions/v1/process-topic/limits`,
    {
      headers: {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
    }
  );
  
  if (!response.ok) {
    throw new Error(`Failed to fetch limits: ${response.status}`);
  }
  
  return response.json();
}

/**
 * Formatea fecha de reset
 */
export function formatResetDate(resetDate: string): string {
  const date = new Date(resetDate);
  const now = new Date();
  const daysUntilReset = Math.ceil((date.getTime() - now.getTime()) / (1000 * 60 * 60 * 24));
  
  if (daysUntilReset <= 1) {
    return 'Los límites se reinician mañana';
  }
  
  return `Los límites se reinician en ${daysUntilReset} días (${date.toLocaleDateString('es-MX')})`;
}