/**
 * ==============================================================================
 * PRODUCTION-READY MATERIAL REQUIREMENT AUTOMATION
 * Covers two sources feeding the same MATERIAL REQUIRMENTS table:
 *   - ENTRY OUT_       : matches NEW BOM by EGIL_CODE (all operations)
 *   - MATERIAL REQUEST : matches NEW BOM by BRANCH + EGIL_CODE + LOCATION_OPERATION
 * Compatible with AppSheet Automation & Google Sheets Batch Operations
 * ==============================================================================
 */

// Configuration Constants
const CONFIG = {
  SS1_ID: "1hdczB6c45AUD_0Owt8vfMIK9NUgsJDTWtWIo9u2HtBA",     // Source & Entry Out Spreadsheet
  SS_TARGET_ID: "1lUyVOjGMK1UbISFgKhsuKwRyUoNKvaIrKfqpdSF6-Ps", // Target Material Requirements Spreadsheet
  SS_BOM_ID: "1rCqlpgWnz9_PvtbH2faN0Y0IOddYLa6dwtwlF49Vek0",    // Master BOM Spreadsheet

  SHEET_ENTRY_OUT: "ENTRY OUT_",             // Source Sheet Name
  SHEET_MATERIAL_REQUEST: "MATERIAL REQUEST", // Source Ad-Hoc Material Request Sheet Name
  SHEET_REQ: "MATERIAL REQUIRMENTS",         // Target Requirements Sheet Name
  SHEET_BOM: "NEW BOM",                      // Source Master BOM Sheet Name

  LOCK_TIMEOUT_MS: 30000,                    // 30 Seconds Execution Lock Safety
  EXCLUDED_CATEGORY: "WOOD"                  // Wood Category Exclusion Rule
};

/**
 * APPSHEET AUTOMATION DIRECT CALL FUNCTION
 * PMS_ID parameter removed. Map parameters accordingly in AppSheet Automation.
 */
function processSingleEntryOutFromAppSheet(timestampVal, branchVal, containerNo, buyerPoVal, skuVal, egilCode, articleNameVal) {
  var lock = LockService.getScriptLock();
  try {
    if (!lock.tryLock(CONFIG.LOCK_TIMEOUT_MS)) {
      Logger.log("Lock acquisition failed. Execution aborted.");
      return;
    }

    var pmsBranch = String(branchVal || "").trim();
    var pmsContainer = String(containerNo || "").trim();
    var pmsEgil = String(egilCode || "").trim();

    // 1. Mandatory Parameter Validation Check
    if (!pmsBranch || !pmsContainer || !pmsEgil) {
      Logger.log("Validation failed: Missing mandatory parameter (BRANCH, CONTAINER_NO, or EGIL_CODE).");
      return;
    }

    var ssTarget = SpreadsheetApp.openById(CONFIG.SS_TARGET_ID);
    var ssBom = SpreadsheetApp.openById(CONFIG.SS_BOM_ID);

    var sheetReq = ssTarget.getSheetByName(CONFIG.SHEET_REQ);
    var sheetBom = ssBom.getSheetByName(CONFIG.SHEET_BOM);

    if (!sheetReq || !sheetBom) {
      Logger.log("Configured target or BOM sheet could not be opened.");
      return;
    }

    // 2. Check for duplicate record in MATERIAL REQUIRMENTS (BRANCH + CONTAINER_NO + EGIL_CODE)
    var reqData = sheetReq.getDataRange().getValues();
    var reqHeaders = reqData[0] || [];
    var idxReqBranch = getHeaderIndex(reqHeaders, ["BRANCH"]);
    var idxReqContainer = getHeaderIndex(reqHeaders, ["CONTAINER_NO", "CONTAINER NO"]);
    var idxReqEgil = getHeaderIndex(reqHeaders, ["EGIL_CODE", "EGIL CODE"]);

    if (idxReqBranch !== -1 && idxReqContainer !== -1 && idxReqEgil !== -1) {
      var lookupKey = pmsBranch.toUpperCase() + "_" + pmsContainer.toUpperCase() + "_" + pmsEgil.toUpperCase();
      for (var r = 1; r < reqData.length; r++) {
        var existingKey = String(reqData[r][idxReqBranch]).trim().toUpperCase() + "_" +
                          String(reqData[r][idxReqContainer]).trim().toUpperCase() + "_" +
                          String(reqData[r][idxReqEgil]).trim().toUpperCase();
        if (existingKey === lookupKey) {
          Logger.log("Duplicate record found for " + lookupKey + ". Skipping generation.");
          return;
        }
      }
    }

    // 3. Search NEW BOM sheet
    var bomData = sheetBom.getDataRange().getValues();
    var bomHeaders = bomData[0] || [];

    var idxBomEgil = getHeaderIndex(bomHeaders, ["EGIL_CODE", "EGIL CODE"]);
    var idxBomCategory = getHeaderIndex(bomHeaders, ["CATEGORY"]);
    var idxBomOperation = getHeaderIndex(bomHeaders, ["LOCATION_OPERATION", "LOCATION OPERATION"]);
    var idxBomMaterial = getHeaderIndex(bomHeaders, ["MATERIAL"]);
    var idxBomUnit = getHeaderIndex(bomHeaders, ["UNIT"]);
    var idxBomConsumption = getHeaderIndex(bomHeaders, ["CONSUMPTION"]);

    var outputRows = [];

    for (var b = 1; b < bomData.length; b++) {
      var bomRow = bomData[b];
      var bomEgil = String(bomRow[idxBomEgil] || "").trim().toUpperCase();
      var bomCategory = String(bomRow[idxBomCategory] || "").trim().toUpperCase();

      // Filter matching EGIL_CODE and skip WOOD category
      if (bomEgil === pmsEgil.toUpperCase() && bomCategory !== CONFIG.EXCLUDED_CATEGORY.toUpperCase()) {
        outputRows.push([
          Utilities.getUuid(),               // 1. REQUIRMENT_ID
          timestampVal || new Date(),        // 2. TIMESTAMP
          pmsBranch,                         // 3. BRANCH
          pmsContainer,                      // 4. CONTAINER_NO
          buyerPoVal,                        // 5. BUYER_PO_NO
          skuVal,                            // 6. SKU_NO
          pmsEgil,                           // 7. EGIL_CODE
          articleNameVal,                    // 8. ARTICLE_NAME
          bomRow[idxBomOperation],            // 9. LOCATION_OPERATION
          bomRow[idxBomMaterial],             // 10. MATERIAL
          bomRow[idxBomUnit],                 // 11. UNIT
          bomRow[idxBomConsumption]          // 12. REQUIRED_QTY
        ]);
      }
    }

    // 4. Write generated material records to MATERIAL REQUIRMENTS (Width: 12 Columns)
    if (outputRows.length > 0) {
      var lastRow = sheetReq.getLastRow();
      sheetReq.getRange(lastRow + 1, 1, outputRows.length, 12).setValues(outputRows);
      Logger.log("AppSheet ENTRY OUT_ Task: Successfully created " + outputRows.length + " requirement rows.");
    } else {
      Logger.log("No non-wood matching BOM records found for EGIL: " + pmsEgil);
    }

  } catch (error) {
    Logger.log("Error processing AppSheet request for ENTRY OUT_: " + error.toString());
  } finally {
    lock.releaseLock();
  }
}

/**
 * GLOBAL BATCH / DELTA SCANNING FUNCTION FOR ENTRY OUT_
 */
function processEntryOutAutomation() {
  var lock = LockService.getScriptLock();
  try {
    if (!lock.tryLock(CONFIG.LOCK_TIMEOUT_MS)) return;

    var ss1 = SpreadsheetApp.openById(CONFIG.SS1_ID);
    var ssTarget = SpreadsheetApp.openById(CONFIG.SS_TARGET_ID);
    var ssBom = SpreadsheetApp.openById(CONFIG.SS_BOM_ID);

    var sheetEntryOut = ss1.getSheetByName(CONFIG.SHEET_ENTRY_OUT);
    var sheetReq = ssTarget.getSheetByName(CONFIG.SHEET_REQ);
    var sheetBom = ssBom.getSheetByName(CONFIG.SHEET_BOM);

    if (!sheetEntryOut || !sheetReq || !sheetBom) {
      throw new Error("One or more configured sheets (ENTRY OUT_, MATERIAL REQUIRMENTS, NEW BOM) could not be found.");
    }

    // Map existing records from MATERIAL REQUIRMENTS for duplicate checking
    var reqData = sheetReq.getDataRange().getValues();
    var reqHeaders = reqData[0] || [];
    var idxReqBranch = getHeaderIndex(reqHeaders, ["BRANCH"]);
    var idxReqContainer = getHeaderIndex(reqHeaders, ["CONTAINER_NO", "CONTAINER NO"]);
    var idxReqEgil = getHeaderIndex(reqHeaders, ["EGIL_CODE", "EGIL CODE"]);

    var existingRecordsSet = new Set();
    for (var r = 1; r < reqData.length; r++) {
      var branchVal = String(reqData[r][idxReqBranch]).trim().toUpperCase();
      var containerVal = String(reqData[r][idxReqContainer]).trim().toUpperCase();
      var egilVal = String(reqData[r][idxReqEgil]).trim().toUpperCase();

      if (branchVal && containerVal && egilVal) {
        existingRecordsSet.add(branchVal + "_" + containerVal + "_" + egilVal);
      }
    }

    // Map BOM rows (Excluding WOOD)
    var bomData = sheetBom.getDataRange().getValues();
    var bomHeaders = bomData[0] || [];
    var idxBomEgil = getHeaderIndex(bomHeaders, ["EGIL_CODE", "EGIL CODE"]);
    var idxBomCategory = getHeaderIndex(bomHeaders, ["CATEGORY"]);
    var idxBomOperation = getHeaderIndex(bomHeaders, ["LOCATION_OPERATION", "LOCATION OPERATION"]);
    var idxBomMaterial = getHeaderIndex(bomHeaders, ["MATERIAL"]);
    var idxBomUnit = getHeaderIndex(bomHeaders, ["UNIT"]);
    var idxBomConsumption = getHeaderIndex(bomHeaders, ["CONSUMPTION"]);

    var bomMap = new Map();
    for (var b = 1; b < bomData.length; b++) {
      var bomRow = bomData[b];
      var bomEgil = String(bomRow[idxBomEgil]).trim().toUpperCase();
      var bomCategory = String(bomRow[idxBomCategory]).trim().toUpperCase();

      if (!bomEgil || bomCategory === CONFIG.EXCLUDED_CATEGORY.toUpperCase()) continue;

      if (!bomMap.has(bomEgil)) bomMap.set(bomEgil, []);
      bomMap.get(bomEgil).push(bomRow);
    }

    // Map ENTRY OUT_ rows
    var entryOutData = sheetEntryOut.getDataRange().getValues();
    var entryOutHeaders = entryOutData[0] || [];
    var entryOutHeaderMap = {};
    entryOutHeaders.forEach(function(h, i) {
      entryOutHeaderMap[String(h).trim().toUpperCase()] = i;
    });

    var idxEntryBranch = entryOutHeaderMap["BRANCH"];
    var idxEntryContainer = entryOutHeaderMap["CONTAINER_NO"] !== undefined ? entryOutHeaderMap["CONTAINER_NO"] : entryOutHeaderMap["CONTAINER NO"];
    var idxEntryEgil = entryOutHeaderMap["EGIL_CODE"] !== undefined ? entryOutHeaderMap["EGIL_CODE"] : entryOutHeaderMap["EGIL CODE"];

    var outputRows = [];
    for (var p = 1; p < entryOutData.length; p++) {
      var entryRow = entryOutData[p];
      var pmsBranch = String(entryRow[idxEntryBranch] || "").trim();
      var pmsContainer = String(entryRow[idxEntryContainer] || "").trim();
      var pmsEgil = String(entryRow[idxEntryEgil] || "").trim();

      if (!pmsBranch || !pmsContainer || !pmsEgil) continue;

      var lookupKey = pmsBranch.toUpperCase() + "_" + pmsContainer.toUpperCase() + "_" + pmsEgil.toUpperCase();
      if (existingRecordsSet.has(lookupKey) || !bomMap.has(pmsEgil.toUpperCase())) continue;

      var matchingBomRows = bomMap.get(pmsEgil.toUpperCase());

      for (var m = 0; m < matchingBomRows.length; m++) {
        var matchedBomRow = matchingBomRows[m];
        outputRows.push([
          Utilities.getUuid(),                                                          // 1. REQUIRMENT_ID
          entryRow[entryOutHeaderMap["TIMESTAMP"]],                                    // 2. TIMESTAMP
          pmsBranch,                                                                   // 3. BRANCH
          pmsContainer,                                                                // 4. CONTAINER_NO
          entryRow[entryOutHeaderMap["BUYER_PO_NO"] || entryOutHeaderMap["BUYER PO NO"]], // 5. BUYER_PO_NO
          entryRow[entryOutHeaderMap["SKU_NO"] || entryOutHeaderMap["SKU NO"]],           // 6. SKU_NO
          pmsEgil,                                                                     // 7. EGIL_CODE
          entryRow[entryOutHeaderMap["ARTICLE_NAME"] || entryOutHeaderMap["ARTICLE NAME"]],// 8. ARTICLE_NAME
          matchedBomRow[idxBomOperation],                                               // 9. LOCATION_OPERATION
          matchedBomRow[idxBomMaterial],                                                // 10. MATERIAL
          matchedBomRow[idxBomUnit],                                                    // 11. UNIT
          matchedBomRow[idxBomConsumption]                                              // 12. REQUIRED_QTY
        ]);
      }
      existingRecordsSet.add(lookupKey);
    }

    if (outputRows.length > 0) {
      var lastRow = sheetReq.getLastRow();
      sheetReq.getRange(lastRow + 1, 1, outputRows.length, 12).setValues(outputRows);
      Logger.log("ENTRY OUT_ Batch: Successfully committed " + outputRows.length + " rows.");
    } else {
      Logger.log("ENTRY OUT_ Batch: No new records to insert.");
    }
  } catch (error) {
    Logger.log("Error in processEntryOutAutomation: " + error.toString());
  } finally {
    lock.releaseLock();
  }
}

/**
 * APPSHEET AUTOMATION DIRECT CALL FUNCTION FOR MATERIAL REQUEST
 * MATERIAL REQUEST rows carry their own LOCATION_OPERATION, so unlike ENTRY OUT_
 * (which pulls the full BOM for an EGIL_CODE), this matches NEW BOM on
 * BRANCH + EGIL_CODE + LOCATION_OPERATION - only the materials needed for the
 * specific operation being requested.
 */
function processSingleMaterialRequestFromAppSheet(timestampVal, branchVal, containerNo, buyerPoVal, skuVal, egilCode, articleNameVal, locationOperationVal) {
  var lock = LockService.getScriptLock();
  try {
    if (!lock.tryLock(CONFIG.LOCK_TIMEOUT_MS)) {
      Logger.log("Lock acquisition failed. Execution aborted.");
      return;
    }

    var pmsBranch = String(branchVal || "").trim();
    var pmsContainer = String(containerNo || "").trim();
    var pmsEgil = String(egilCode || "").trim();
    var pmsOperation = String(locationOperationVal || "").trim();

    // 1. Mandatory Parameter Validation Check
    if (!pmsBranch || !pmsContainer || !pmsEgil || !pmsOperation) {
      Logger.log("Validation failed: Missing mandatory parameter (BRANCH, CONTAINER_NO, EGIL_CODE, or LOCATION_OPERATION).");
      return;
    }

    var ssTarget = SpreadsheetApp.openById(CONFIG.SS_TARGET_ID);
    var ssBom = SpreadsheetApp.openById(CONFIG.SS_BOM_ID);

    var sheetReq = ssTarget.getSheetByName(CONFIG.SHEET_REQ);
    var sheetBom = ssBom.getSheetByName(CONFIG.SHEET_BOM);

    if (!sheetReq || !sheetBom) {
      Logger.log("Configured target or BOM sheet could not be opened.");
      return;
    }

    // 2. Check for duplicate record in MATERIAL REQUIRMENTS (BRANCH + CONTAINER_NO + EGIL_CODE + LOCATION_OPERATION)
    var reqData = sheetReq.getDataRange().getValues();
    var reqHeaders = reqData[0] || [];
    var idxReqBranch = getHeaderIndex(reqHeaders, ["BRANCH"]);
    var idxReqContainer = getHeaderIndex(reqHeaders, ["CONTAINER_NO", "CONTAINER NO"]);
    var idxReqEgil = getHeaderIndex(reqHeaders, ["EGIL_CODE", "EGIL CODE"]);
    var idxReqOperation = getHeaderIndex(reqHeaders, ["LOCATION_OPERATION", "LOCATION OPERATION"]);

    if (idxReqBranch !== -1 && idxReqContainer !== -1 && idxReqEgil !== -1 && idxReqOperation !== -1) {
      var lookupKey = pmsBranch.toUpperCase() + "_" + pmsContainer.toUpperCase() + "_" + pmsEgil.toUpperCase() + "_" + pmsOperation.toUpperCase();
      for (var r = 1; r < reqData.length; r++) {
        var existingKey = String(reqData[r][idxReqBranch]).trim().toUpperCase() + "_" +
                          String(reqData[r][idxReqContainer]).trim().toUpperCase() + "_" +
                          String(reqData[r][idxReqEgil]).trim().toUpperCase() + "_" +
                          String(reqData[r][idxReqOperation]).trim().toUpperCase();
        if (existingKey === lookupKey) {
          Logger.log("Duplicate record found for " + lookupKey + ". Skipping generation.");
          return;
        }
      }
    }

    // 3. Search NEW BOM sheet, matching BRANCH + EGIL_CODE + LOCATION_OPERATION, excluding WOOD category
    var bomData = sheetBom.getDataRange().getValues();
    var bomHeaders = bomData[0] || [];

    var idxBomBranch = getHeaderIndex(bomHeaders, ["BRANCH"]);
    var idxBomEgil = getHeaderIndex(bomHeaders, ["EGIL_CODE", "EGIL CODE"]);
    var idxBomCategory = getHeaderIndex(bomHeaders, ["CATEGORY"]);
    var idxBomOperation = getHeaderIndex(bomHeaders, ["LOCATION_OPERATION", "LOCATION OPERATION"]);
    var idxBomMaterial = getHeaderIndex(bomHeaders, ["MATERIAL"]);
    var idxBomUnit = getHeaderIndex(bomHeaders, ["UNIT"]);
    var idxBomConsumption = getHeaderIndex(bomHeaders, ["CONSUMPTION"]);

    var outputRows = [];

    for (var b = 1; b < bomData.length; b++) {
      var bomRow = bomData[b];
      var bomBranch = String(bomRow[idxBomBranch] || "").trim().toUpperCase();
      var bomEgil = String(bomRow[idxBomEgil] || "").trim().toUpperCase();
      var bomCategory = String(bomRow[idxBomCategory] || "").trim().toUpperCase();
      var bomOperation = String(bomRow[idxBomOperation] || "").trim().toUpperCase();

      // Filter matching BRANCH + EGIL_CODE + LOCATION_OPERATION and skip WOOD category
      if (bomBranch === pmsBranch.toUpperCase() &&
          bomEgil === pmsEgil.toUpperCase() &&
          bomOperation === pmsOperation.toUpperCase() &&
          bomCategory !== CONFIG.EXCLUDED_CATEGORY.toUpperCase()) {
        outputRows.push([
          Utilities.getUuid(),               // 1. REQUIRMENT_ID
          timestampVal || new Date(),        // 2. TIMESTAMP
          pmsBranch,                         // 3. BRANCH
          pmsContainer,                      // 4. CONTAINER_NO
          buyerPoVal,                        // 5. BUYER_PO_NO
          skuVal,                            // 6. SKU_NO
          pmsEgil,                           // 7. EGIL_CODE
          articleNameVal,                    // 8. ARTICLE_NAME
          bomRow[idxBomOperation],            // 9. LOCATION_OPERATION
          bomRow[idxBomMaterial],             // 10. MATERIAL
          bomRow[idxBomUnit],                 // 11. UNIT
          bomRow[idxBomConsumption]          // 12. REQUIRED_QTY
        ]);
      }
    }

    // 4. Write generated material records to MATERIAL REQUIRMENTS (Width: 12 Columns)
    if (outputRows.length > 0) {
      var lastRow = sheetReq.getLastRow();
      sheetReq.getRange(lastRow + 1, 1, outputRows.length, 12).setValues(outputRows);
      Logger.log("AppSheet MATERIAL REQUEST Task: Successfully created " + outputRows.length + " requirement rows.");
    } else {
      Logger.log("No non-wood matching BOM records found for BRANCH/EGIL_CODE/LOCATION_OPERATION: " + pmsBranch + "/" + pmsEgil + "/" + pmsOperation);
    }

  } catch (error) {
    Logger.log("Error processing AppSheet request for MATERIAL REQUEST: " + error.toString());
  } finally {
    lock.releaseLock();
  }
}

/**
 * GLOBAL BATCH / DELTA SCANNING FUNCTION FOR MATERIAL REQUEST
 */
function processMaterialRequestAutomation() {
  var lock = LockService.getScriptLock();
  try {
    if (!lock.tryLock(CONFIG.LOCK_TIMEOUT_MS)) return;

    var ss1 = SpreadsheetApp.openById(CONFIG.SS1_ID);
    var ssTarget = SpreadsheetApp.openById(CONFIG.SS_TARGET_ID);
    var ssBom = SpreadsheetApp.openById(CONFIG.SS_BOM_ID);

    var sheetMaterialRequest = ss1.getSheetByName(CONFIG.SHEET_MATERIAL_REQUEST);
    var sheetReq = ssTarget.getSheetByName(CONFIG.SHEET_REQ);
    var sheetBom = ssBom.getSheetByName(CONFIG.SHEET_BOM);

    if (!sheetMaterialRequest || !sheetReq || !sheetBom) {
      throw new Error("One or more configured sheets (MATERIAL REQUEST, MATERIAL REQUIRMENTS, NEW BOM) could not be found.");
    }

    // Map existing records from MATERIAL REQUIRMENTS for duplicate checking (BRANCH + CONTAINER_NO + EGIL_CODE + LOCATION_OPERATION)
    var reqData = sheetReq.getDataRange().getValues();
    var reqHeaders = reqData[0] || [];
    var idxReqBranch = getHeaderIndex(reqHeaders, ["BRANCH"]);
    var idxReqContainer = getHeaderIndex(reqHeaders, ["CONTAINER_NO", "CONTAINER NO"]);
    var idxReqEgil = getHeaderIndex(reqHeaders, ["EGIL_CODE", "EGIL CODE"]);
    var idxReqOperation = getHeaderIndex(reqHeaders, ["LOCATION_OPERATION", "LOCATION OPERATION"]);

    var existingRecordsSet = new Set();
    for (var r = 1; r < reqData.length; r++) {
      var branchVal = String(reqData[r][idxReqBranch]).trim().toUpperCase();
      var containerVal = String(reqData[r][idxReqContainer]).trim().toUpperCase();
      var egilVal = String(reqData[r][idxReqEgil]).trim().toUpperCase();
      var operationVal = idxReqOperation !== -1 ? String(reqData[r][idxReqOperation]).trim().toUpperCase() : "";

      if (branchVal && containerVal && egilVal) {
        existingRecordsSet.add(branchVal + "_" + containerVal + "_" + egilVal + "_" + operationVal);
      }
    }

    // Map BOM rows by BRANCH + EGIL_CODE + LOCATION_OPERATION (Excluding WOOD)
    var bomData = sheetBom.getDataRange().getValues();
    var bomHeaders = bomData[0] || [];
    var idxBomBranch = getHeaderIndex(bomHeaders, ["BRANCH"]);
    var idxBomEgil = getHeaderIndex(bomHeaders, ["EGIL_CODE", "EGIL CODE"]);
    var idxBomCategory = getHeaderIndex(bomHeaders, ["CATEGORY"]);
    var idxBomOperation = getHeaderIndex(bomHeaders, ["LOCATION_OPERATION", "LOCATION OPERATION"]);
    var idxBomMaterial = getHeaderIndex(bomHeaders, ["MATERIAL"]);
    var idxBomUnit = getHeaderIndex(bomHeaders, ["UNIT"]);
    var idxBomConsumption = getHeaderIndex(bomHeaders, ["CONSUMPTION"]);

    var bomMap = new Map();
    for (var b = 1; b < bomData.length; b++) {
      var bomRow = bomData[b];
      var bomBranch = String(bomRow[idxBomBranch] || "").trim().toUpperCase();
      var bomEgil = String(bomRow[idxBomEgil] || "").trim().toUpperCase();
      var bomCategory = String(bomRow[idxBomCategory] || "").trim().toUpperCase();
      var bomOperation = String(bomRow[idxBomOperation] || "").trim().toUpperCase();

      if (!bomBranch || !bomEgil || !bomOperation || bomCategory === CONFIG.EXCLUDED_CATEGORY.toUpperCase()) continue;

      var bomKey = bomBranch + "_" + bomEgil + "_" + bomOperation;
      if (!bomMap.has(bomKey)) bomMap.set(bomKey, []);
      bomMap.get(bomKey).push(bomRow);
    }

    // Map MATERIAL REQUEST rows
    var requestData = sheetMaterialRequest.getDataRange().getValues();
    var requestHeaders = requestData[0] || [];
    var idxReqRowBranch = getHeaderIndex(requestHeaders, ["BRANCH"]);
    var idxReqRowContainer = getHeaderIndex(requestHeaders, ["CONTAINER_NO", "CONTAINER NO"]);
    var idxReqRowEgil = getHeaderIndex(requestHeaders, ["EGIL_CODE", "EGIL CODE"]);
    var idxReqRowOperation = getHeaderIndex(requestHeaders, ["LOCATION_OPERATION", "LOCATION OPERATION"]);
    var idxReqRowTimestamp = getHeaderIndex(requestHeaders, ["TIMESTAMP"]);
    var idxReqRowBuyerPo = getHeaderIndex(requestHeaders, ["BUYER_PO_NO", "BUYER PO NO", "BUYER_PO", "BUYER PO"]);
    var idxReqRowSku = getHeaderIndex(requestHeaders, ["SKU_NO", "SKU NO"]);
    var idxReqRowArticle = getHeaderIndex(requestHeaders, ["ARTICLE_NAME", "ARTICLE NAME"]);

    var outputRows = [];
    for (var p = 1; p < requestData.length; p++) {
      var requestRow = requestData[p];
      var pmsBranch = String(requestRow[idxReqRowBranch] || "").trim();
      var pmsContainer = String(requestRow[idxReqRowContainer] || "").trim();
      var pmsEgil = String(requestRow[idxReqRowEgil] || "").trim();
      var pmsOperation = idxReqRowOperation !== -1 ? String(requestRow[idxReqRowOperation] || "").trim() : "";

      if (!pmsBranch || !pmsContainer || !pmsEgil || !pmsOperation) continue;

      var lookupKey = pmsBranch.toUpperCase() + "_" + pmsContainer.toUpperCase() + "_" + pmsEgil.toUpperCase() + "_" + pmsOperation.toUpperCase();
      var bomKey = pmsBranch.toUpperCase() + "_" + pmsEgil.toUpperCase() + "_" + pmsOperation.toUpperCase();
      if (existingRecordsSet.has(lookupKey) || !bomMap.has(bomKey)) continue;

      var matchingBomRows = bomMap.get(bomKey);

      for (var m = 0; m < matchingBomRows.length; m++) {
        var matchedBomRow = matchingBomRows[m];
        outputRows.push([
          Utilities.getUuid(),                                                                  // 1. REQUIRMENT_ID
          idxReqRowTimestamp !== -1 ? requestRow[idxReqRowTimestamp] : new Date(),               // 2. TIMESTAMP
          pmsBranch,                                                                            // 3. BRANCH
          pmsContainer,                                                                         // 4. CONTAINER_NO
          idxReqRowBuyerPo !== -1 ? requestRow[idxReqRowBuyerPo] : "",                           // 5. BUYER_PO_NO
          idxReqRowSku !== -1 ? requestRow[idxReqRowSku] : "",                                   // 6. SKU_NO
          pmsEgil,                                                                              // 7. EGIL_CODE
          idxReqRowArticle !== -1 ? requestRow[idxReqRowArticle] : "",                           // 8. ARTICLE_NAME
          matchedBomRow[idxBomOperation],                                                        // 9. LOCATION_OPERATION
          matchedBomRow[idxBomMaterial],                                                         // 10. MATERIAL
          matchedBomRow[idxBomUnit],                                                             // 11. UNIT
          matchedBomRow[idxBomConsumption]                                                       // 12. REQUIRED_QTY
        ]);
      }
      existingRecordsSet.add(lookupKey);
    }

    if (outputRows.length > 0) {
      var lastRow = sheetReq.getLastRow();
      sheetReq.getRange(lastRow + 1, 1, outputRows.length, 12).setValues(outputRows);
      Logger.log("MATERIAL REQUEST Batch: Successfully committed " + outputRows.length + " rows.");
    } else {
      Logger.log("MATERIAL REQUEST Batch: No new records to insert.");
    }
  } catch (error) {
    Logger.log("Error in processMaterialRequestAutomation: " + error.toString());
  } finally {
    lock.releaseLock();
  }
}

/**
 * HELPER: Robust Case-Insensitive Header Finder
 */
function getHeaderIndex(headers, possibleNames) {
  for (var i = 0; i < headers.length; i++) {
    var headerStr = String(headers[i] || "").trim().toUpperCase();
    for (var j = 0; j < possibleNames.length; j++) {
      if (headerStr === possibleNames[j].toUpperCase()) {
        return i;
      }
    }
  }
  return -1;
}
